from pipeline_setup import *

# Create an class with anobjective function suitable for giving to the scipy minimizer
# Including a cache to avoid recomputing the same values
class FFCObjectiveWithCache:
    """A class to compute the objective function and constraints for FFC optimization with caching.
    Parameters:
        names_x (list): List of parameter names to be optimized.
        datablock_init (dict): Initial data block containing fixed parameters.
        params_default (dict): Default parameters for the optimization.
        sector_emissions_dict (dict): Dictionary containing emissions data for different sectors.
        verbosity (int): Level of verbosity for output messages.
    """
    def __init__(self, names_x, datablock_init, params_default, verbosity=0):
        self.names_x = names_x
        self.datablock_init = datablock_init
        self.params_default = params_default
        self._cache = {}
        self.verbosity = verbosity

        # Define the names of the z variables returned by the calculator
        self.z_names = ["SSR weight",
                   "SSR prot",
                   "SSR fat",
                   "SSR kcal",
                   "emissions",
                   "herd size",
                   "animals"]

    def _calculate(self, x_tuple, verbosity):
        
        x = list(x_tuple)
        # Only recompute if not already cached
        
        if x_tuple not in self._cache:

            # Update the parameters with the current values
            params = self.params_default.copy()
            for i_name, name_string in enumerate(self.names_x):
                params[name_string] = x[i_name]
            
            # Perform the SSR and emissions calculation
            z_val = run_calculator(self.datablock_init, params)

            # cached dict
            zval_dict = {zn: zv for zn, zv in zip(self.z_names, z_val)}

            # Store the results in the cache
            self._cache[x_tuple] = zval_dict

        # Print out what's going on 
        if (verbosity > 1):
            for i_name, name_string in enumerate(self.names_x):
                print(f"{name_string} = {x[i_name]:.10f}; ", end="")
            for i_name, name_string in enumerate(list(self._cache[x_tuple].keys())):
                print(f"{name_string} = {self._cache[x_tuple][name_string]:.10f}; ", end="")
            print()

        return self._cache[x_tuple]

    # Define the objective function for minimization
    def objective(self, x, z_name_requested, verbosity=None):
        x_tuple = tuple(x)
        if verbosity is None:
            verbosity = self.verbosity
        return self._calculate(x_tuple, verbosity)[z_name_requested]
    
    # Define the objective function for minimization
    def negative_objective(self, x, z_name_requested, verbosity=None):
        x_tuple = tuple(x)
        if verbosity is None:
            verbosity = self.verbosity
        return -self._calculate(x_tuple, verbosity)[z_name_requested]

    # Define the objective function for SSR constraint
    def positive_constraint(self, x, key, threshold, verbosity=None):
        x_tuple = tuple(x)
        if verbosity is None:
            verbosity = self.verbosity
        return self._calculate(x_tuple, verbosity)[key] - threshold
    
    def negative_constraint(self, x, key, threshold, verbosity=None):
        x_tuple = tuple(x)
        if verbosity is None:
            verbosity = self.verbosity
        return threshold - self._calculate(x_tuple, verbosity)[key]
    