import matplotlib.pyplot as plt
import xarray as xr
import numpy as np
from scipy.ndimage import zoom
import pandas as pd
from pipeline_setup import *
import time
from scipy.optimize import minimize
from FFCObjectWithCache import FFCObjectiveWithCache
import argparse
from datetime import datetime

parser = argparse.ArgumentParser()
parser.add_argument('--ranges', type=str, help='Name of parameter ranges on range spreadsheet', default="JPSarah1618 Thu19Jun25")
parser.add_argument('--zreq', type=str, help='Name of parameter to optimize', default="herd size")
parser.add_argument('--niter', type=int, help='Number of iterations', default=10)
parser.add_argument('--test', type=bool, help='Run test with baseline scenario', default=False)
parser.add_argument('--ffc_tol', type=float, help='Tolerance for FFC objective', default=1e-6)

parser.add_argument('--base_param', nargs=2, action='append', metavar=('KEY', 'VALUE'), default=[])
parser.add_argument('--adv_set', nargs=2, action='append', metavar=('KEY', 'VALUE'), default=[])

default_run_name = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
parser.add_argument('--run_name', type=str, help='Name of run to save results', default=default_run_name)

args = parser.parse_args()

# ---------------------------------------------------
# Advanced settings
# ---------------------------------------------------

print("Reading advanced settings...")

# Set file locations
advanced_settings_url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTanjc08kc5vIN-icUMzMEGA9bJuDesLX8V_u2Ab6zSC4MOhLZ8Jrr18DL9o4ofKIrSq6FsJXhPWu3F/pub?gid=0&single=true&output=csv"

# Read in emissions from other sectors
sector_emissions_dict = set_sector_emissions_dict()
# Read in the advanced settings from the google sheet

adv_set_dict = read_advanced_settings(advanced_settings_url)

# Update the advanced settings with the passed arguments
passed_adv_set = {k: float(v) for k, v in args.adv_set}
adv_set_dict.update(passed_adv_set)

print("Advanced settings:")
for k, v in adv_set_dict.items():
    print(k, v)
print()


# ---------------------------------------------------
# Parameter ranges
# ---------------------------------------------------
print("Reading parameter ranges...")

# Read parameter ranges from scenarios spreadsheet
ranges_worksheet_url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRXLuSuuxfTx1tUilnO1KojbaGiO-o-rtf1OtsQ0YHetV-OozWH1BXc7N-1Y9jG9Ue2ys7mcf-SzPc3/pub?gid=1034155472&single=true&output=csv"
ranges = pd.read_csv(ranges_worksheet_url, dtype='string', skiprows=2)
ranges.head()

# Remove "Max " and "Min " prefixes and extract unique names
unique_names = ranges["Name"].dropna().str.replace(r"^(Max |Min )", "", regex=True).unique()
unique_names
# Create a dictionary to store the ranges
ranges_dict = {}

# Iterate over unique range names
for name in unique_names:
    # Filter rows corresponding to the current range name
    min_row = ranges[ranges["Name"] == f"Min {name}"].iloc[0]
    max_row = ranges[ranges["Name"] == f"Max {name}"].iloc[0]
    
    # Extract parameter ranges as tuples
    param_ranges = {
        col: (float(min_row[col]), float(max_row[col]))
        for col in ranges.columns[3:]  # Skip the "Name" column
        if pd.notna(min_row[col]) and pd.notna(max_row[col])  # Ensure values are not NaN
    }
    
    # Add to the dictionary
    ranges_dict[name] = param_ranges

# List of parameter names and ranges
def names_bounds(param_range_dict):

    param_range_dict_with_range = {k: v for k, v in param_range_dict.items() if v[0] != v[1]}

    names_x = list(param_range_dict_with_range.keys())
    x_bounds = list(param_range_dict_with_range.values())

    names_fixed = [k for k, v in param_range_dict.items() if v[0] == v[1]]
    values_fixed = [v[0] for k, v in param_range_dict.items() if v[0] == v[1]]

    return names_x, x_bounds, names_fixed, values_fixed

# names_x, x_bounds = names_bounds(param_range_dict)
names_x, x_bounds, names_fixed, values_fixed = names_bounds(ranges_dict[args.ranges])

print("Parameter ranges:")
for n, b in zip(names_x, x_bounds):
    print(n, b)
print()

# Set the scenario parameters
params_baseline = set_baseline_scenario(adv_set_dict)

# Update baseline parameters
for k, v in zip(names_fixed, values_fixed):
    params_baseline[k] = v

passed_base_params = {k: float(v) for k, v in args.base_param}
params_baseline.update(passed_base_params)

print("Baseline scenario:")
for k, v in params_baseline.items():
    print(k, v)
print()

# ---------------------------------------------------
# Set up the datablock
# ---------------------------------------------------

# Set the datablock
datablock_init = datablock_setup()

# Also add the baseline parameters to the datablock
datablock_init.update(params_baseline)

ffc_wrapper = FFCObjectiveWithCache(names_x, datablock_init, params_baseline, verbosity=2)

z_name_requested = args.zreq

x0 = [params_baseline[n] for n in names_x]
ffc_constraints = [{'type': 'ineq', 'fun': lambda x: ffc_wrapper.positive_constraint(x, "SSR weight", threshold=0.6736643225, verbosity=0)},
                   {'type': 'ineq', 'fun': lambda x: ffc_wrapper.positive_constraint(x, "SSR prot", threshold=0.7331495528, verbosity=0)},
                   {'type': 'ineq', 'fun': lambda x: ffc_wrapper.positive_constraint(x, "SSR fat", threshold=0.6333747730, verbosity=0)},
                   {'type': 'ineq', 'fun': lambda x: ffc_wrapper.positive_constraint(x, "SSR kcal", threshold=0.6854386315, verbosity=0)},
                   {'type': 'ineq', 'fun': lambda x: ffc_wrapper.negative_constraint(x, "emissions", threshold=0.0, verbosity=0)}]

# ---------------------------------------------------
# Configure and run the optimization
# ---------------------------------------------------

if args.test:

    z1_name = "SSR weight"
    z2_name = "SSR prot"
    z3_name = "SSR fat"
    z4_name = "SSR kcal"
    z5_name = "emissions"
    z6_name = "herd size"
    z7_name = "animals"

    z_names = [z1_name, z2_name, z3_name, z4_name, z5_name, z6_name, z7_name]

    z_val_baseline = run_calculator(datablock_init, params_baseline)
    for zn, zval in zip(z_names, z_val_baseline):
        print(f"{zn} = {zval:.8f}; ", end="")
    print()
    exit()

# Set tolerances, verbosity, and options for the minimizer
ffc_tol = args.ffc_tol
options = {
    'disp': True,      # Show convergence messages
    'maxiter': args.niter,     # Max number of iterations
    'rhobeg' : 10 # Reasonable step size (mostly they are percentages, so change by 10%)
}

result = minimize(
    lambda x: ffc_wrapper.negative_objective(x, z_name_requested),
    # lambda x: ffc_wrapper.objective(x, z_name_requested),
    x0,
    method='COBYLA',
    bounds=x_bounds,
    constraints=ffc_constraints,
    tol=ffc_tol,
    options=options
)

# The result is an OptimizeResult object
print("Optimization success:", result.success)
print("Message:", result.message)
print("Number of iterations:", result.nfev)
print("Optimal value of x:", result.x)
print("Minimum value of function:", result.fun)

print("Optimized parameters: ", result['x'])      # Optimal parameters
print("Optimized value: ", result['fun'])    # Minimum value of the objective
print("Was the minimization succesfull? ", result['success'])  # Boolean indicating if it was successful

# Display the results
#z1_val, z2_val = list(ffc_results.values())[:2]
z1_val = ffc_wrapper.objective(result.x, "SSR weight")
z2_val = ffc_wrapper.objective(result.x, "emissions")
print(f"SSR weight = {z1_val:.8f}; emissions = {z2_val:.8f}")

# ---------------------------------------------------
# Save results to log file
# ---------------------------------------------------

log_file_path = f"{args.run_name}.log"

with open(log_file_path, "w") as log_file:
    # Write baseline parameters
    log_file.write("Baseline Parameters:\n")
    for k, v in params_baseline.items():
        log_file.write(f"{k}: {v}\n")
    log_file.write("\n")

    # Write varied parameters and their bounds
    log_file.write("Varied Parameters and Bounds:\n")
    for n, b in zip(names_x, x_bounds):
        log_file.write(f"{n}: {b}\n")
    log_file.write("\n")

    # Write optimization results
    log_file.write("Optimization Results:\n")
    for n, val in zip(names_x, result.x):
        log_file.write(f"{n}: {val}\n")
    log_file.write("\n")

    # Write optimization results
    log_file.write(f"Minimum value of function: {result.fun}\n")
    log_file.write(f"Optimization success: {result.success}\n")
    log_file.write(f"Message: {result.message}\n")
    log_file.write(f"Number of iterations: {result.nfev}\n")
    log_file.write(f"Minimiser tolerance: {ffc_tol}\n")

print(f"Results saved to {log_file_path}")

