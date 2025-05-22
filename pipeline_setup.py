from agrifoodpy.pipeline import Pipeline
from datablock_setup import *
from model import *

#from datablock_setup import datablock_setup
from pipeline_setup import pipeline_setup
import matplotlib.pyplot as plt
import xarray as xr
import copy
import numpy as np
import pandas as pd

def read_advanced_settings(advanced_settings_url):
    """Reads the advanced settings from the spreadsheet URL"""
    advanced_settings  = pd.read_csv(advanced_settings_url, dtype='string')
    advanced_settings_dict = {}

    for index, row in advanced_settings.iterrows():
        if row["type"] == "float": 
            advanced_settings_dict[row["key"]] = float(row["value"])
        elif row["type"] == "string":
            advanced_settings_dict[row["key"]] = str(row["value"])
        elif row["type"] == "bool":
            advanced_settings_dict[row["key"]] = row["value"] == "TRUE"
    
    return advanced_settings_dict

def set_sector_emissions_dict():

    sector_emissions_dict = {
        "F-gases":1.60,
        "Waste":5.11,
        "Shipping":0.51,
        "Aviation":22.67,
        "LU sources":20.82, # Sum of 2025 land use sources
        "Agriculture":0, # Set to be calculated by the model
        "Fuel supply":1.57, 
        "Electricity":1.00,
        "Manufacturing":3.57,
        "Buildings":1.11, # Sum of residential and non-residential
        "Transport":1.07,
        "LU sinks":0, # Set to be calculated by the model
        "Removals":0, # Set to be calculated by the model
    }
    return sector_emissions_dict

# Set the pipeline
def SSR_emissions(input_datablock, params, sector_emissions_dict):

    datablock_copy = copy.deepcopy(input_datablock)
    food_system = Pipeline(datablock_copy)

    # SSR
    food_system = pipeline_setup(food_system, params)
    food_system.run()
    datablock_result = food_system.datablock

    metric_yr = 2050
    gcapday = datablock_result["food"]["g/cap/day"].sel(Year=metric_yr).fillna(0)
    SSR_metric_yr = gcapday.fbs.SSR()


    # Emissions
    seq_da = datablock_result["impact"]["co2e_sequestration"].sel(Year=metric_yr)
    emissions = datablock_result["impact"]["g_co2e/year"]["production"].sel(Year=metric_yr)/1e6
    total_emissions = emissions.sum(dim="Item").values/1e6
    total_seq = seq_da.sel(Item=["Broadleaf woodland",
                                    "Coniferous woodland",
                                    "Managed pasture",
                                    "Managed arable",
                                    "Mixed farming",
                                    "Silvopasture",
                                    "Agroforestry"]).sum(dim="Item").values/1e6

    total_removals = seq_da.sel(Item=["BECCS from waste", "BECCS from overseas biomass", "BECCS from land", "DACCS"]).sum(dim="Item").values/1e6

    emissions_balance = xr.DataArray(data = list(sector_emissions_dict.values()),
                            name="Sectoral emissions",
                            coords={"Sector": list(sector_emissions_dict.keys())})

    emissions_balance.loc[{"Sector": "Agriculture"}] = total_emissions
    emissions_balance.loc[{"Sector": "LU sinks"}] = -total_seq
    emissions_balance.loc[{"Sector": "Removals"}] = -total_removals

    emissions_balance.loc[{"Sector": "LU sources"}] -= seq_da.sel(Item=["Restored upland peat", "Restored lowland peat"]).sum(dim="Item").values/1e6

    total_emissions = emissions_balance.sum()

    return SSR_metric_yr.to_numpy(), total_emissions.to_numpy()


# Set the scenario parameters - ideally switch to using spreadsheet instead of this
def set_baseline_scenario(params):

    params["yield_proj"] = 0
    params["elasticity"] = 0
    params["ruminant"] = 0
    params["pig_poultry"] = 0
    params["fish_seafood"] = 0
    params["dairy"] = 0
    params["eggs"] = 0
    params["fruit_veg"] = 0
    params["pulses"] = 0
    params["meat_alternatives"] = 0
    params["dairy_alternatives"] = 0
    params["waste"] = 0
    params["foresting_pasture"] = 13.17
    params["land_BECCS"] = 0
    params["horticulture"] = 0
    params["pulse_production"] = 0
    params["lowland_peatland"] = 0
    params["upland_peatland"] = 0
    params["pasture_soil_carbon"] = 0
    params["arable_soil_carbon"] = 0
    params["mixed_farming"] = 0
    params["silvopasture"] = 0
    params["nitrogen"] = 0
    params["methane_inhibitor"] = 0
    params["stock_density"] = 0
    params["manure_management"] = 0
    params["animal_breeding"] = 0
    params["fossil_livestock"] = 0
    params["agroforestry"] = 0
    params["vertical_farming"] = 0
    params["fossil_arable"] = 0
    params["waste_BECCS"] = 0
    params["overseas_BECCS"] = 0
    params["DACCS"] = 0
    params["bdleaf_conif_ratio"] = 75

    # Set some additional parameters that aren't in the spreadsheet 
    params["scaling_nutrient"] = "kCal/cap/day"
    params["cereal_scaling"] = True

    return params


def pipeline_setup(food_system, params):

    # Global parameters
    food_system.datablock_write(["global_parameters", "timescale"], params["n_scale"])

    # Consumer demand
    food_system.add_node(project_future,
                            {"yield_change":params["yield_proj"]})
    
    food_system.add_node(item_scaling,
                            {"scale":1+params["ruminant"]/100,
                            "items":[2731, 2732],
                            "source":["production", "imports"],
                            "elasticity":[params["elasticity"], 1-params["elasticity"]],
                            "scaling_nutrient":params["scaling_nutrient"],
                            "constant":params["cereal_scaling"],
                            "non_sel_items":("Item_group", "Cereals - Excluding Beer")})

    food_system.add_node(item_scaling,
                            {"scale":1+params["pig_poultry"]/100,
                            "items":[2733, 2734],
                            "source":["production", "imports"],
                            "elasticity":[params["elasticity"], 1-params["elasticity"]],
                            "scaling_nutrient":params["scaling_nutrient"],
                            "constant":params["cereal_scaling"],
                            "non_sel_items":("Item_group", "Cereals - Excluding Beer")})

    food_system.add_node(item_scaling,
                            {"scale":1+params["fish_seafood"]/100,
                            "items":("Item_group", "Fish, Seafood"),
                            "source":["production", "imports"],
                            "elasticity":[params["elasticity"], 1-params["elasticity"]],
                            "scaling_nutrient":params["scaling_nutrient"],
                            "constant":params["cereal_scaling"],
                            "non_sel_items":("Item_group", "Cereals - Excluding Beer")})

    food_system.add_node(item_scaling,
                            {"scale":1+params["dairy"]/100,
                            "items":[2740, 2743, 2948],
                            "source":["production", "imports"],
                            "elasticity":[params["elasticity"], 1-params["elasticity"]],
                            "scaling_nutrient":params["scaling_nutrient"],
                            "constant":params["cereal_scaling"],
                            "non_sel_items":("Item_group", "Cereals - Excluding Beer")})

    food_system.add_node(item_scaling,
                            {"scale":1+params["eggs"]/100,
                            "items":[2949],
                            "source":["production", "imports"],
                            "elasticity":[params["elasticity"], 1-params["elasticity"]],
                            "scaling_nutrient":params["scaling_nutrient"],
                            "constant":params["cereal_scaling"],
                            "non_sel_items":("Item_group", "Cereals - Excluding Beer")})

    food_system.add_node(item_scaling,
                            {"scale":1+params["fruit_veg"]/100,
                            "items":("Item_group", ["Vegetables", "Fruits - Excluding Wine"]),
                            "source":["production", "imports"],
                            "elasticity":[params["elasticity"], 1-params["elasticity"]],
                            "scaling_nutrient":params["scaling_nutrient"],
                            "constant":params["cereal_scaling"],
                            "non_sel_items":("Item_group", "Cereals - Excluding Beer")})

    food_system.add_node(item_scaling,
                            {"scale":1+params["pulses"]/100,
                            "items":("Item_group", ["Pulses"]),
                            "source":["production", "imports"],
                            "elasticity":[params["elasticity"], 1-params["elasticity"]],
                            "scaling_nutrient":params["scaling_nutrient"],
                            "constant":params["cereal_scaling"],
                            "non_sel_items":("Item_group", "Cereals - Excluding Beer")})

    food_system.add_node(cultured_meat_model,
                            {"cultured_scale":params["meat_alternatives"]/100,
                            "labmeat_co2e":params["labmeat_co2e"],
                            "items":[2731, 2732, 2733, 2734],
                            "copy_from":2731,
                            "new_items":5000,
                            "new_item_name":"Alternative meat",
                            "source":["production", "imports"],
                            "elasticity":[params["elasticity"], 1-params["elasticity"]]})

    food_system.add_node(cultured_meat_model,
                            {"cultured_scale":params["dairy_alternatives"]/100,
                            "labmeat_co2e":params["dairy_alternatives_co2e"],
                            "items":[2948, 2743, 2740],
                            "copy_from":2948,
                            "new_items":5001,
                            "new_item_name":"Alternative dairy",
                            "source":["production", "imports"],
                            "elasticity":[params["elasticity"], 1-params["elasticity"]]})
    
    if not params["cereal_scaling"]:
        food_system.add_node(item_scaling,
                            {"scale":1+params["cereals"]/100,
                            "items":("Item_group", "Cereals - Excluding Beer"),
                            "source":["production", "imports"],
                            "elasticity":[params["elasticity"], 1-params["elasticity"]],
                            "scaling_nutrient":params["scaling_nutrient"]})


    food_system.add_node(food_waste_model,
                            {"waste_scale":params["waste"],
                            "kcal_rda":params["rda_kcal"],
                            "source":["production", "imports"],
                            "elasticity":[params["elasticity"], 1-params["elasticity"]]})


    # Land management
    food_system.add_node(forest_land_model_new,
                            {"forest_fraction":params["foresting_pasture"]/100,
                            "bdleaf_conif_ratio":params["bdleaf_conif_ratio"]/100,
                            })

    food_system.add_node(BECCS_farm_land,
                        {"farm_percentage":params["land_BECCS"]/100,
                        })

    food_system.add_node(shift_production,
                         {"scale":params["horticulture"]/100,
                          "items":("Item_group", ["Vegetables",
                                                  "Fruits - Excluding Wine",
                                                  "Vegetables Oils",
                                                  "Spices",
                                                  "Starchy Roots",
                                                  "Sugar Crops",
                                                  "Oilcrops",
                                                  "Treenuts",
                                                  ]),
                          "items_target":("Item_group", ["Cereals - Excluding Beer",
                                                         "Pulses",
                                                         ]),
                          "land_area_ratio":0.08650301817
                          })
    
    food_system.add_node(shift_production,
                         {"scale":params["pulse_production"]/100,
                          "items":("Item_group", "Pulses"),
                          "items_target":("Item_group", ["Cereals - Excluding Beer",
                                                         "Vegetables",
                                                         "Fruits - Excluding Wine",
                                                         "Vegetables Oils",
                                                         "Spices",
                                                         "Starchy Roots",
                                                         "Sugar Crops",
                                                         "Oilcrops",
                                                         "Treenuts",                                                      
                                                         ]),
                          "land_area_ratio":0.03327492402
                          })

    food_system.add_node(peatland_restoration,
                        {"restore_fraction":0.0475*params["lowland_peatland"]/100,
                         "new_land_type":"Restored lowland peat",
                         "old_land_type":["Arable"],
                         "items":"Vegetal Products",
                         })
    
    food_system.add_node(peatland_restoration,
                        {"restore_fraction":0.0273*params["upland_peatland"]/100,
                         "new_land_type":"Restored upland peat",
                         "old_land_type":["Improved grassland", "Semi-natural grassland"],
                         "items":"Animal Products",
                         })
    
    food_system.add_node(managed_agricultural_land_carbon_model,
                        {"fraction":params["pasture_soil_carbon"]/100,
                         "managed_class":"Managed pasture",
                         "old_class":["Improved grassland", "Semi-natural grassland"]})
    
    food_system.add_node(managed_agricultural_land_carbon_model,
                        {"fraction":params["arable_soil_carbon"]/100,
                         "managed_class":"Managed arable",
                         "old_class":"Arable"})


    food_system.add_node(mixed_farming_model,
                        {"fraction":params["mixed_farming"]/100,
                         "prod_scale_factor":params["mixed_farming_production_scale"],
                         "items":("Item_origin","Vegetal Products"),
                         "secondary_prod_scale_factor":params["mixed_farming_secondary_production_scale"],
                         "secondary_items":("Item_origin","Animal Products")})

    # Livestock farming practices        
    
    food_system.add_node(agroecology_model,
                            {"land_percentage":params["silvopasture"]/100.,
                            "agroecology_class":"Silvopasture",
                            "land_type":["Improved grassland",
                                         "Semi-natural grassland",
                                         "Managed pasture"],
                            "tree_coverage":params["agroecology_tree_coverage"],
                            "replaced_items":[2731, 2732],
                            "seq_ha_yr":params["agroecology_tree_coverage"]*(params["bdleaf_conif_ratio"]/100 * params["bdleaf_seq_ha_yr"] \
                                        + (1 - params["bdleaf_conif_ratio"]/100) * params["conif_seq_ha_yr"]) \
                                        + (1 - params["agroecology_tree_coverage"]) * params["managed_pasture_seq_ha_yr"],
                            })
    
    food_system.add_node(scale_impact,
                         {"items":("Item_origin","Vegetal Products"),
                          "scale_factor":params["nitrogen_ghg_factor"]*params["nitrogen"]/100})

    food_system.add_node(scale_impact,
                            {"items":[2731, 2732],
                            "scale_factor":params["methane_ghg_factor"]*params["methane_inhibitor"]/100})
    
    food_system.add_node(scale_production,
                            {"scale_factor":1+params["stock_density"]/100,
                             "items":[2731, 2732, 2733, 2735, 2948, 2740, 2743],
                             "elasticity":[params["elasticity"], 1-params["elasticity"]]})

    # food_system.add_node(scale_production,
    #                         {"scale_factor":1-params["methane_prod_factor*params["methane_inhibitor/100,
    #                         "items":[2731, 2732]})

    food_system.add_node(scale_impact,
                            {"items":[2731, 2732, 2733, 2735, 2948, 2740, 2743],
                            "scale_factor":params["manure_ghg_factor"]*params["manure_management"]/100})

    # food_system.add_node(scale_production,
    #                         {"scale_factor":1-params["manure_prod_factor*params["manure_management/100,
    #                         "items":[2731, 2732, 2733, 2735, 2948, 2740, 2743]})

    food_system.add_node(scale_impact,
                            {"items":[2731, 2732],
                            "scale_factor":params["breeding_ghg_factor"]*params["animal_breeding"]/100})

    # food_system.add_node(scale_production,
    #                         {"scale_factor":1-params["breeding_prod_factor*params["animal_breeding/100,
    #                         "items":[2731, 2732]})
    
    food_system.add_node(scale_impact,
                            {"items":("Item_origin","Animal Products"),
                            "scale_factor":params["fossil_livestock_ghg_factor"]*params["fossil_livestock"]/100})

    # food_system.add_node(scale_production,
    #                         {"scale_factor":1 - params["fossil_livestock_prod_factor*params["fossil_livestock/100,
    #                         "items":("Item_origin","Animal Products")})

    # Arable farming practices
    food_system.add_node(agroecology_model,
                            {"land_percentage":params["agroforestry"]/100.,
                            "agroecology_class":"Agroforestry",
                            "land_type":["Arable",
                                         "Managed arable"],
                            "tree_coverage":params["agroecology_tree_coverage"],
                            "replaced_items":2511,
                            "seq_ha_yr":params["agroecology_tree_coverage"]*(params["bdleaf_conif_ratio"]/100 * params["bdleaf_seq_ha_yr"] \
                                        + (1 - params["bdleaf_conif_ratio"]/100) * params["conif_seq_ha_yr"]) \
                                        + (1 - params["agroecology_tree_coverage"]) * params["managed_pasture_seq_ha_yr"],
                            
                            })
    
    # food_system.add_node(zero_land_farming_model,
    #                      {"fraction":params["vertical_farming/100,
    #                       "items":("Item_group", ["Vegetables", "Fruits - Excluding Wine"]),
    #                       "bdleaf_conif_ratio":params["bdleaf_conif_ratio/100})
    

    food_system.add_node(extra_urban_farming,
                         {"fraction":params["vertical_farming"]/100,
                          "items":("Item_group", ["Vegetables", "Fruits - Excluding Wine"])
                          })

    food_system.add_node(scale_impact,
                            {"items":("Item_origin", "Vegetal Products"),
                            "scale_factor":params["fossil_arable_ghg_factor"]*params["fossil_arable"]/100})

    # food_system.add_node(scale_production,
    #                         {"scale_factor":1 - params["fossil_arable_prod_factor*params["fossil_arable/100,
    #                         "items":("Item_origin", "Vegetal Products")})

    # Technology & Innovation    
    food_system.add_node(ccs_model,
                            {"waste_BECCS":params["waste_BECCS"]*1e6,
                            "overseas_BECCS":params["overseas_BECCS"]*1e6,
                            "DACCS":params["DACCS"]*1e6})


    # Compute emissions and sequestration
    food_system.add_node(forest_sequestration_model,
                            {"land_type":["Broadleaf woodland",
                                          "Coniferous woodland",
                                          "Restored upland peat",
                                          "Restored lowland peat",
                                          "Managed arable",
                                          "Managed pasture",
                                          "Mixed farming",
                                          ],
                            "seq":[params["bdleaf_seq_ha_yr"],
                                   params["conif_seq_ha_yr"],
                                   params["peatland_seq_ha_yr"],
                                   params["peatland_seq_ha_yr"],
                                   params["managed_arable_seq_ha_yr"],
                                   params["managed_pasture_seq_ha_yr"],
                                   params["mixed_farming_seq_ha_yr"],
                                   ]})

    food_system.add_node(compute_emissions)

    return food_system
