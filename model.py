import xarray as xr
import numpy as np
from agrifoodpy.food.food import FoodBalanceSheet
from agrifoodpy.utils.scaling import logistic_scale, linear_scale
import warnings
import copy

def project_future(datablock, yield_change=None):
    """Project future food consumption based on scale
    
    Parameters
    ----------
    datablock : Dict
        Dictionary containing xarray datasets for population, food consumption etc.

    scale : xarray.DataArray
        Scale to apply to food consumption
    yield_change : float
        Percentage change by the end of the projection period.

    Returns
    -------
    datablock : Dict
        New dictionary containinng projected food consumption data
    """
    years = np.arange(2021,2051)

    pop = datablock["population"]["population"]

    scale = pop.sel(Region=826, Year=np.arange(2021, 2051)) / \
               pop.sel(Region=826, Year=2020)
    
    # Per capita per day values remain constant
    g_cap_day = datablock["food"]["g/cap/day"]
    g_prot_cap_day = datablock["food"]["g_prot/cap/day"]
    g_fat_cap_day = datablock["food"]["g_fat/cap/day"]
    kcal_cap_day = datablock["food"]["kCal/cap/day"]

    years_past = g_cap_day.Year.values

    g_cap_day = g_cap_day.fbs.add_years(years, "constant")
    g_prot_cap_day = g_prot_cap_day.fbs.add_years(years, "constant")
    g_fat_cap_day = g_fat_cap_day.fbs.add_years(years, "constant")
    kcal_cap_day = kcal_cap_day.fbs.add_years(years, "constant")

    # Scale food production
    scale_past = xr.DataArray(np.ones(len(years_past)), dims=["Year"], coords={"Year": years_past})
    scale_tot = xr.concat([scale_past, scale], dim="Year")

    cereal_items = g_cap_day.sel(Item=g_cap_day.Item_group=="Cereals - Excluding Beer").Item.values

    # If yield_change is not None, add a scaling factor to account for yield increase, only to vegetal items
    if yield_change is not None:
        scale_tot = scale_tot.expand_dims({"Item": g_cap_day.Item.values})
        scale_yield = xr.ones_like(scale_tot)
        scale_yield.loc[{"Item": cereal_items}] = linear_scale(2020, 2020, 2050, 2050, c_init=1, c_end=1+yield_change)
        scale_tot = scale_tot / scale_yield

    # Scale food production and balance using imports
    g_cap_day = g_cap_day.fbs.scale_add(element_in="production", element_out="imports", scale=1/scale_tot, add=False)
    g_prot_cap_day = g_prot_cap_day.fbs.scale_add(element_in="production", element_out="imports", scale=1/scale_tot, add=False)
    g_fat_cap_day = g_fat_cap_day.fbs.scale_add(element_in="production", element_out="imports", scale=1/scale_tot, add=False)
    kcal_cap_day = kcal_cap_day.fbs.scale_add(element_in="production", element_out="imports", scale=1/scale_tot, add=False)

    # Do the same with exports, but this time add the change in exports to imports
    g_cap_day = g_cap_day.fbs.scale_add(element_in="exports", element_out="imports", scale=1/scale_tot)
    g_prot_cap_day = g_prot_cap_day.fbs.scale_add(element_in="exports", element_out="imports", scale=1/scale_tot)
    g_fat_cap_day = g_fat_cap_day.fbs.scale_add(element_in="exports", element_out="imports", scale=1/scale_tot)
    kcal_cap_day = kcal_cap_day.fbs.scale_add(element_in="exports", element_out="imports", scale=1/scale_tot)

    # Emissions per gram of food also remain constant
    g_co2e_g = datablock["impact"]["gco2e/gfood"]
    g_co2e_g = g_co2e_g.fbs.add_years(years, "constant")

    datablock["food"]["g/cap/day"] = g_cap_day
    datablock["food"]["g_prot/cap/day"] = g_prot_cap_day
    datablock["food"]["g_fat/cap/day"] = g_fat_cap_day
    datablock["food"]["kCal/cap/day"] = kcal_cap_day
    datablock["impact"]["gco2e/gfood"] = g_co2e_g
    datablock["impact"]["baseline"] = copy.deepcopy(datablock["impact"]["gco2e/gfood"])

    datablock["food"]["baseline_projected"] = copy.deepcopy(datablock["food"]["g/cap/day"])

    return datablock

def item_scaling_multiple(datablock, scale, source, scaling_nutrient,
                 elasticity=None, items=None, constant=True,
                 non_sel_items=None):
    """Reduces per capita intake quantities and replaces them by other items
    keeping the overall consumption constant. Scales land use if production
    changes
    
    Parameters
    ----------
    scale : float, arr, or xarray.DataArray
        Scaling factor to apply to the selected items. If a DataArray, it must
        have a "Year" dimension with the years to scale.
        
    items : arr, tuple
        if an array, 
    """

    timescale = datablock["global_parameters"]["timescale"]
    # We can use any quantity here, either per cap/day or per year. The ratio
    # will cancel out the population growth
    food_orig = datablock["food"][scaling_nutrient]

    if np.isscalar(source):
        source = [source]
    
    out = food_orig.copy(deep=True)

    non_sel_items = get_items(food_orig, non_sel_items)
    
    # Balanced scaling. Reduce food, reduce imports, keep kCal constant
    for sc, it in zip(scale, items):
    
        it_arr = get_items(out, it)
        # if no items are specified, do nothing
        if items is None:
            return datablock
        
        out = balanced_scaling(fbs=out,
                               items=it_arr,
                               element="food",
                               timescale=timescale,
                               year=2021,
                               scale=sc,
                               adoption="logistic",
                               origin=source,
                               add=True,
                               elasticity=elasticity,
                               constant=constant,
                               non_sel_items=non_sel_items)

    # Scale feed, seed and processing
    out = feed_scale(out, food_orig)

    # out = check_negative_source(out, "production", "imports")
    out = check_negative_source(out, "imports", "exports", add=False)

    ratio = out / food_orig
    ratio = ratio.where(~np.isnan(ratio), 1)

    # Update per cap/day values and per year values using the same ratio, which
    # is independent of population growth
    datablock["food"]["g/cap/day"] *= ratio

    return datablock

def item_scaling(datablock, scale, source, scaling_nutrient,
                 elasticity=None, items=None, constant=True,
                 non_sel_items=None):
    """Reduces per capita intake quantities and replaces them by other items
    keeping the overall consumption constant. Scales land use if production
    changes"""

    timescale = datablock["global_parameters"]["timescale"]
    # We can use any quantity here, either per cap/day or per year. The ratio
    # will cancel out the population growth
    food_orig = datablock["food"][scaling_nutrient]

    if np.isscalar(source):
        source = [source]
    
    items = get_items(food_orig, items)
    non_sel_items = get_items(food_orig, non_sel_items)
    # if no items are specified, do nothing
    if items is None:
        return datablock
    
    # Balanced scaling. Reduce food, reduce imports, keep kCal constant
    out = balanced_scaling(fbs=food_orig,
                           items=items,
                           element="food",
                           timescale=timescale,
                           year=2021,
                           scale=scale,
                           adoption="logistic",
                           origin=source,
                           add=True,
                           elasticity=elasticity,
                           constant=constant,
                           non_sel_items=non_sel_items)

    # Scale feed, seed and processing
    out = feed_scale(out, food_orig)

    # out = check_negative_source(out, "production", "imports")
    out = check_negative_source(out, "imports", "exports", add=False)

    ratio = out / food_orig
    ratio = ratio.where(~np.isnan(ratio), 1)

    # Update per cap/day values and per year values using the same ratio, which
    # is independent of population growth
    qty_key = ["g/cap/day", "g_prot/cap/day", "g_fat/cap/day", "kCal/cap/day"]
    for key in qty_key:
        datablock["food"][key] *= ratio

    return datablock

def balanced_scaling(fbs, items, scale, element, year=None, adoption=None,
                     timescale=10, origin=None, add=True, elasticity=None,
                     constant=False, non_sel_items=None, fallback=None,
                     add_fallback=True):
    """Scale items quantities across multiple elements in a FoodBalanceSheet
    Dataset 
    
    Scales selected item quantities on a food balance sheet and with the
    posibility to keep the sum of selected elements constant.
    Optionally, produce an Dataset with a sequence of quantities over the years
    following a smooth scaling according to the selected functional form.

    The elements used to supply the modified quantities can be selected to keep
    a balanced food balance sheet.

    Parameters
    ----------
    fbs : xarray.Dataset
        Input food balance sheet Dataset.
    items : list
        List of items to scale in the food balance sheet.
    element : string
        Name of the DataArray to scale.
    scale : float
        Scaling parameter after full adoption.
    adoption : string, optional
        Shape of the scaling adoption curve. "logistic" uses a logistic model
        for a slow-fast-slow adoption. "linear" uses a constant slope adoption
        during the the "timescale period"
    year : int, optional
        Year of the Food Balance Sheet to use as pivot. If not set, the last
        year of the array is used
    timescale : int, optional
        Timescale for the scaling to be applied completely.  If "year" +
        "timescale" is greater than the last year in the array, it is extended
        to accomodate the extra years.
    origin : string, optional
        Name of the DataArray which will be used to balance the food balance
        sheets. Any change to the "element" DataArray will be reflected in this
        DataArray.
    add : bool, optional
        If set to True, the scaled element difference is added to the "origin"
        DataArray. If False, it is subtracted.
    elasticity : float, float array_like optional
        Fractional percentage of the difference that is added to each
        element in origin.
    constant : bool, optional
        If set to True, the sum of element remains constant by scaling the non
        selected items accordingly.
    non_sel_items : list, optional
        List of items to scale to achieve constant quantity sum when constant is
        set to True.
    fallback : string, optional
        Name of the DataArray used to provide the excess required to balance the
        food balance sheet in case the "origin" falls below zero.
    add_fallback : bool, optional
        If set to True, the excessis added to the fallback DataArray. If False,
        it is subtracted.

    Returns
    -------
    data : xarray.Dataarray
        Food balance sheet Dataset with scaled "food" values.
    """

    # Check for single item inputs
    if np.isscalar(items):
        items = [items]

    if np.isscalar(origin):
        origin = [origin]

    if np.isscalar(add):
        add = [add]*len(origin)

    # Check for single item list fbs
    input_item_list = fbs.Item.values
    if np.isscalar(input_item_list):
        input_item_list = [input_item_list]
        if constant:
            warnings.warn("Constant set to true but input only has a single item.")
            constant = False

    # If no items are provided, we scale all of them.
    if items is None or np.sort(items) is np.sort(input_item_list):
        items = fbs.Item.values
        if constant:
            warnings.warn("Cannot keep food constant when scaling all items.")
            constant = False

    # Define Dataarray to use as pivot
    if "Year" in fbs.dims:
        if year is None:
            if np.isscalar(fbs.Year.values):
                year = fbs.Year.values
                fbs_toscale = fbs
            else:
                year = fbs.Year.values[-1]
                fbs_toscale = fbs.isel(Year=-1)
        else:
            fbs_toscale = fbs.sel(Year=year)

    else:
        fbs_toscale = fbs
        try:
            year = fbs.Year.values
        except AttributeError:
            year=0

    # Define scale array based on year range
    if adoption is not None:
        if adoption == "linear":
            from agrifoodpy.utils.scaling import linear_scale as scale_func
        elif adoption == "logistic":
            from agrifoodpy.utils.scaling import logistic_scale as scale_func
        else:
            raise ValueError("Adoption must be one of 'linear' or 'logistic'")
        
        y0 = fbs.Year.values[0]
        y1 = year
        y2 = np.min([year + timescale, fbs.Year.values[-1]])
        y3 = fbs.Year.values[-1]
        
        scale_arr = scale_func(y0, y1, y2, y3, c_init=1, c_end = scale)
        
        # # Extend the dataset to include all the years of the array
        # fbs_toscale = fbs_toscale * xr.ones_like(scale_arr)
    
    else:
        scale_arr = scale    

    # Modify and return
    out = fbs.fbs.scale_add(element, origin, scale_arr, items, add=add,
                            elasticity=elasticity)    

    if constant:

        delta = out[element] - fbs[element]

        # Scale non selected items
        if non_sel_items is None:
            non_sel_items = np.setdiff1d(fbs.Item.values, items)

        non_sel_scale = (fbs.sel(Item=non_sel_items)[element].sum(dim="Item") - delta.sum(dim="Item")) / fbs.sel(Item=non_sel_items)[element].sum(dim="Item")
        
        # Make sure inf and nan values are not scaled
        non_sel_scale = non_sel_scale.where(np.isfinite(non_sel_scale)).fillna(1.0)

        if np.any(non_sel_scale < 0):
            warnings.warn("Additional consumption cannot be compensated by \
                        reduction of non-selected items")
        
        out = out.fbs.scale_add(element, origin, non_sel_scale, non_sel_items, add=add,
                            elasticity=elasticity)

        # If fallback is defined, adjust to prevent negative values
        if fallback is not None:
            df = sum(out[org].where(out[org] < 0).fillna(0) for org in origin)
            out[fallback] -= np.where(add_fallback, -1, 1)*df
            for org in origin:
                out[org] = out[org].where(out[org] > 0, 0)

    return out

def food_waste_model(datablock, waste_scale, kcal_rda, source, elasticity=None):
    """Reduces daily per capita per day intake energy above a set threshold.
    """

    timescale = datablock["global_parameters"]["timescale"]
    kcal_fact = datablock["food"]["kCal/g_food"]
    food_orig = copy.deepcopy(datablock["food"]["g/cap/day"])*kcal_fact
    datablock["food"]["rda_kcal"] = kcal_rda

    # This is the maximum factor we can multiply food by to achieve consumption
    # equal to rda_kcal, multiplied by the ambition level
    waste_factor = (food_orig["food"].isel(Year=-1).sum(dim="Item") - kcal_rda) \
                 / food_orig["food"].isel(Year=-1).sum(dim="Item") \
                 * (waste_scale / 100)
    
    waste_factor = waste_factor.to_numpy()

    # Create a logistic curve starting at 1, ending at 1-waste_factor
    scale_waste = logistic_food_supply(food_orig, timescale, 1, 1-waste_factor)

    # Set to "imports" or "production" to choose which element of the food system supplies the change in consumption
    # Scale food and subtract difference from production
    out = food_orig.fbs.scale_add(element_in="food",
                                  element_out=source,
                                  scale=scale_waste,
                                  elasticity=elasticity)
    
    # Scale feed, seed and processing
    out = feed_scale(out, food_orig)

    # If supply element is negative, set to zero and add the negative delta to imports
    out = check_negative_source(out, "imports", "exports", add=False)

    # Scale all per capita qantities proportionally
    ratio = out / food_orig
    ratio = ratio.where(~np.isnan(ratio), 1)

    datablock["food"]["g/cap/day"] *= ratio

    return datablock

def alternative_food_model(datablock, cultured_scale, labmeat_co2e, baseline_items, copy_from,
                        new_items, new_item_name, replaced_items, source, elasticity=None):
    """Replaces selected items by alternative products on a weight by weight
    basis, compared to a baseline array.
    A list of replaced items is adjusted to keep calories constant.
    """

    timescale = datablock["global_parameters"]["timescale"]
    baseline_items = get_items(datablock["food"]["g/cap/day"], baseline_items)
    items_to_replace = get_items(datablock["food"]["g/cap/day"], replaced_items)

    nutrition_keys = ["g_prot/g_food", "g_fat/g_food", "kCal/g_food"]
    # Add new items to the food dataset
    datablock["food"]["g/cap/day"] = datablock["food"]["g/cap/day"].fbs.add_items(new_items)
    datablock["food"]["g/cap/day"]["Item_name"].loc[{"Item":new_items}] = new_item_name
    datablock["food"]["g/cap/day"]["Item_origin"].loc[{"Item":new_items}] = "Alternative Food"
    datablock["food"]["g/cap/day"]["Item_group"].loc[{"Item":new_items}] = "Alternative Food"
    # Set values to zero to avoid issues
    datablock["food"]["g/cap/day"].loc[{"Item":new_items}] = 0

    # Add nutrition values for new products to the food dataset
    for key in nutrition_keys:
        datablock["food"][key] = datablock["food"][key].fbs.add_items(new_items, copy_from=[copy_from])
        datablock["food"][key]["Item_name"].loc[{"Item":new_items}] = new_item_name
        datablock["food"][key]["Item_origin"].loc[{"Item":new_items}] = "Alternative Food"
        datablock["food"][key]["Item_group"].loc[{"Item":new_items}] = "Alternative Food"

    # Scale products by cultured_scale
    food_orig = copy.deepcopy(datablock["food"]["g/cap/day"])
    kcal_fact = datablock["food"]["kCal/g_food"]
    kcal_orig = food_orig * kcal_fact
    food_base = copy.deepcopy(datablock["food"]["baseline_projected"])

    scale_alternative = logistic_food_supply(food_orig, timescale, 0, cultured_scale)
    
    # This is the new alternative food consumption
    delta_alternative = (food_base["food"].sel(Item=baseline_items) * scale_alternative).sum(dim="Item")

    out = food_orig.copy(deep=True)
    out["food"].loc[{"Item":new_items}] += delta_alternative

    # If no item elasticity is provided, divide elasticity equally
    if elasticity is None:
        elasticity = [1.0/len(source)] * len(source)
    elif np.isscalar(elasticity):
        elasticity = [elasticity] * len(source)

    # Adjust source elements based on elasticity
    for src, elst in zip(source, elasticity):
        out[src].loc[{"Item":new_items}] += delta_alternative*elst

    # Reduce cereals to compensate additional kCal from alternative food
    delta_kcal_alternative = delta_alternative * kcal_fact.sel(Item=new_items)
    orig_target_calories = kcal_orig["food"].sel(Item=items_to_replace).sum(dim="Item")
    final_target_calories = kcal_orig["food"].sel(Item=items_to_replace).sum(dim="Item") - delta_kcal_alternative
    scale_target_calories = final_target_calories / orig_target_calories

    # out["food"].loc[{"Item":items_to_replace}] *= scale_target_calories
    out = out.fbs.scale_add(element_in="food",
                            element_out=source,
                            items=items_to_replace,
                            scale=scale_target_calories,
                            elasticity=elasticity)
    
    kcal_cap_day = kcal_orig.fbs.scale_add(element_in="food",
                            element_out=source,
                            items=items_to_replace,
                            scale=scale_target_calories,
                            elasticity=elasticity)

    # Check negative source elements
    out = check_negative_source(out, "production")
    out = check_negative_source(out, "imports", "exports", add=False)

    # Adjust feed and seed from animal production
    out = feed_scale(out, food_orig)
    datablock["food"]["g/cap/day"] = out

    # Add emissions factor for cultured meat
    datablock["impact"]["gco2e/gfood"] = datablock["impact"]["gco2e/gfood"].fbs.add_items(new_items)
    datablock["impact"]["gco2e/gfood"].loc[{"Item":new_items}] = labmeat_co2e

    out_kcal_cap_day = scale_kcal_feed(kcal_cap_day, kcal_orig, new_items)
    ratio = out_kcal_cap_day / kcal_cap_day
    ratio = ratio.where(~np.isnan(ratio), 1)

    datablock["food"]["g/cap/day"] *= ratio

    return datablock

def cultured_meat_model(datablock, cultured_scale, labmeat_co2e, items, copy_from,
                        new_items, new_item_name, source, elasticity=None):
    """Replaces selected items by cultured products on a weight by weight
    basis. 
    """

    timescale = datablock["global_parameters"]["timescale"]
    items_to_replace = items

    # Add cultured meat to the dataset
    qty_key = ["g/cap/day", "g_prot/cap/day", "g_fat/cap/day", "kCal/cap/day"]
    
    for key in qty_key:
        datablock["food"][key] = datablock["food"][key].fbs.add_items(new_items)
        datablock["food"][key]["Item_name"].loc[{"Item":new_items}] = new_item_name
        datablock["food"][key]["Item_origin"].loc[{"Item":new_items}] = "Alternative Food"
        datablock["food"][key]["Item_group"].loc[{"Item":new_items}] = "Alternative Food"
        # Set values to zero to avoid issues
        datablock["food"][key].loc[{"Item":new_items}] = 0

    # Scale products by cultured_scale
    food_orig = copy.deepcopy(datablock["food"]["g/cap/day"])
    kcal_orig = copy.deepcopy(datablock["food"]["kCal/cap/day"])

    scale_labmeat = logistic_food_supply(food_orig, timescale, 1, 1-cultured_scale)

    # Scale and remove from suplying element
    out = food_orig.fbs.scale_add(element_in="food",
                                  element_out=source,
                                  scale=scale_labmeat,
                                  items=items_to_replace,
                                  add=True,
                                  elasticity=elasticity)
    
    # Add delta to cultured meat
    delta = (datablock["food"]["g/cap/day"]-out).sel(Item=items_to_replace).sum(dim="Item")
    out.loc[{"Item":new_items}] += delta

    # If production is negative, set to zero and add the negative delta to
    # imports
    out = check_negative_source(out, "production")
    out = check_negative_source(out, "imports", "exports", add=False)

    # Reduce feed and seed
    out = feed_scale(out, food_orig)

    datablock["food"]["g/cap/day"] = out

    # Add nutrition values for cultured meat
    nutrition_keys = ["g_prot/g_food", "g_fat/g_food", "kCal/g_food"]
    for key in nutrition_keys:
        datablock["food"][key] = datablock["food"][key].fbs.add_items(new_items, copy_from=[copy_from])
        datablock["food"][key]["Item_name"].loc[{"Item":new_items}] = new_item_name
        datablock["food"][key]["Item_origin"].loc[{"Item":new_items}] = "Alternative Food"
        datablock["food"][key]["Item_group"].loc[{"Item":new_items}] = "Alternative Food"

    # Add emissions factor for cultured meat
    datablock["impact"]["gco2e/gfood"] = datablock["impact"]["gco2e/gfood"].fbs.add_items(new_items)
    datablock["impact"]["gco2e/gfood"].loc[{"Item":new_items}] = labmeat_co2e

    # Recompute per capita values
    for key_pc, key_n in zip(qty_key[1:], nutrition_keys):
        datablock["food"][key_pc] = datablock["food"]["g/cap/day"] * datablock["food"][key_n]

    kcal_cap_day = datablock["food"]["kCal/cap/day"]

    out_kcal_cap_day = scale_kcal_feed(kcal_cap_day, kcal_orig, new_items)
    ratio = out_kcal_cap_day / kcal_cap_day
    ratio = ratio.where(~np.isnan(ratio), 1)

    for key in qty_key:
        datablock["food"][key] *= ratio

    return datablock

def compute_emissions(datablock):
    """
    Computes the emissions per capita per day and per year for each food item,
    using the per capita daily weights and PN18 emissions factors.
    """
    pop = datablock["population"]["population"]
    pop_world = pop.sel(Region = 826)

    # Compute emissions per capita per day
    co2e_cap_day = datablock["food"]["g/cap/day"] * datablock["impact"]["gco2e/gfood"]

    # Compute emissions per year
    datablock["food"]["g_co2e/cap/day"] = co2e_cap_day
    datablock["impact"]["g_co2e/year"] = co2e_cap_day * pop_world * 365.25

    return datablock

def compute_t_anomaly(datablock):
    """Computes the temperature anomaly, concentration and radiation forcing from
    the per year emissions using the FAIR model.
    """

    from agrifoodpy.impact.model import fair_co2_only

    # g co2e / year
    g_co2e_year = datablock["impact"]["g_co2e/year"]["production"].sum(
        dim="Item")

    # Gt co2e / year
    Gt_co2e_year = g_co2e_year * 1e-15

    # Compute temperature anomaly based on emissions
    T, C, F = fair_co2_only(Gt_co2e_year)

    T = T.rename({"timebounds": "Year"})
        
    datablock["impact"]["T"] = T
    datablock["impact"]["C"] = C
    datablock["impact"]["F"] = F

    return datablock

def forest_land_model_new(datablock, forest_fraction, bdleaf_conif_ratio):

    """Replaces arable and livestock land with forest land.
    If positive, spare_fraction only replaces pasture land and changes it to forest land.
    If negative, spare_fraction only replaces forest land and changes it to a mix of
    pasture land and arable land, which depends on the original land distribution.
    """
    
    timescale = datablock["global_parameters"]["timescale"]
    pctg = datablock["land"]["percentage_land_use"].copy(deep=True)


    old_use_arable = datablock["land"]["percentage_land_use"].sel({"aggregate_class":["Arable"]}).sum()

    total_uk_land = pctg.sum()

    # Fraction of forest to achieve area delta
    forest_xy = datablock["land"]["percentage_land_use"].sel({"aggregate_class":["Broadleaf woodland", "Coniferous woodland"]})
    total_forest = forest_xy.sum()

    # Required delta to forest = requested fraction - current fraction
    delta_forest_land_percentage = forest_fraction - float(total_forest / total_uk_land)

    # Total area in hectares to be converted
    delta_forest_area = total_uk_land * delta_forest_land_percentage

    pasture_xy = datablock["land"]["percentage_land_use"].sel({"aggregate_class":["Improved grassland", "Semi-natural grassland"]})
    old_use_pasture = pasture_xy.sum()
    
    if delta_forest_land_percentage > 0:
        # We only change pasture to forest
        
        # Only replace pasture
        delta_pasture_ratio = delta_forest_area / old_use_pasture
        delta_pasture_xy = pasture_xy * delta_pasture_ratio

        delta_forest_xy = delta_pasture_xy.sum(dim="aggregate_class") * forest_xy / forest_xy.sum(dim="aggregate_class")

        pctg.loc[{"aggregate_class":["Improved grassland", "Semi-natural grassland"]}] -= delta_pasture_xy.fillna(0)

        # Check if sum across aggregate_class equals 100 and adjust Broadleaf woodland if needed
        sum_across_classes = pctg.sum(dim="aggregate_class")
        difference = 100 - sum_across_classes
        pctg.loc[{"aggregate_class": "Broadleaf woodland"}] += difference.where(~np.isnan(pctg.sel(aggregate_class="Broadleaf woodland")), 0) * bdleaf_conif_ratio
        pctg.loc[{"aggregate_class": "Coniferous woodland"}] += difference.where(~np.isnan(pctg.sel(aggregate_class="Coniferous woodland")), 0) * (1 - bdleaf_conif_ratio)
        
    else:
        # We change forest to a mix of arable and forest
        agricultural_xy = datablock["land"]["percentage_land_use"].sel({"aggregate_class":["Improved grassland", "Semi-natural grassland", "Arable"]})
    
        # Per pixel percentage delta
        delta_forest_ratio = delta_forest_area / total_forest
        delta_forest_xy = forest_xy * delta_forest_ratio
        delta_agriculture_xy = delta_forest_xy.sum(dim="aggregate_class") * agricultural_xy / agricultural_xy.sum(dim="aggregate_class")

        pctg.loc[{"aggregate_class":["Broadleaf woodland", "Coniferous woodland"]}] += delta_forest_xy
        pctg.loc[{"aggregate_class":["Improved grassland", "Semi-natural grassland", "Arable"]}] -= delta_agriculture_xy

    # Add spared class to the land use map
    datablock["land"]["percentage_land_use"] = pctg

    # Scale food production and imports
    new_use_pasture = pctg.sel({"aggregate_class":["Improved grassland", "Semi-natural grassland"]}).sum()
    new_use_arable = pctg.sel({"aggregate_class":"Arable"}).sum()
    
    scale_use_pasture = (new_use_pasture/old_use_pasture).to_numpy()
    scale_use_arable = (new_use_arable/old_use_arable).to_numpy()

    food_orig = datablock["food"]["g/cap/day"]
    scale_forest_pasture = logistic_food_supply(food_orig, timescale, 1, scale_use_pasture)
    scale_forest_arable = logistic_food_supply(food_orig, timescale, 1, scale_use_arable)

    scaled_items_pasture = food_orig.sel(Item=food_orig.Item_origin=="Animal Products").Item.values
    scaled_items_arable = food_orig.sel(Item=food_orig.Item_origin=="Vegetal Products").Item.values

    out = food_orig.fbs.scale_add(element_in="production",
                                  element_out="imports",
                                  scale=scale_forest_pasture,
                                  items=scaled_items_pasture,
                                  add=False)
    
    out = out.fbs.scale_add(element_in="production",
                                  element_out="imports",
                                  scale=scale_forest_arable,
                                  items=scaled_items_arable,
                                  add=False)
    
    out = check_negative_source(out, "production")
    out = check_negative_source(out, "imports")

    ratio = out / food_orig
    ratio = ratio.where(~np.isnan(ratio), 1)

    datablock["food"]["g/cap/day"] = out

    return datablock

def forest_land_model(datablock, forest_fraction, bdleaf_conif_ratio,
                      map_mask=None, mask_vals=None):
    """Replaces arable and livestock land with forest land.
    If positive, spare_fraction only replaces pasature land and changes it to forest land.
    If negative, spare_fraction only replaces forest land and changes it to a mix of
    pasture land and arable land, which depends on the original land distribution.
    """
    
    timescale = datablock["global_parameters"]["timescale"]
    pctg = datablock["land"]["percentage_land_use"].copy(deep=True)
    old_use_pasture = datablock["land"]["percentage_land_use"].sel({"aggregate_class":["Improved grassland", "Semi-natural grassland"]}).sum()
    old_use_arable = datablock["land"]["percentage_land_use"].sel({"aggregate_class":["Arable"]}).sum()

    # if no alc grade is provided, then use the whole map
    if mask_vals is not None or map_mask is not None:
        alc = datablock["land"][map_mask]
        alc_mask = np.isin(alc, mask_vals)
    else:
        alc_mask = np.ones_like(pctg, dtype=bool)

    total_uk_land = pctg.sum()

    total_forestable_pasture_land = pctg.where(alc_mask, other=0).sel({"aggregate_class":["Improved grassland", "Semi-natural grassland"]}).sum()
    total_forestable_arable_land = pctg.where(alc_mask, other=0).sel({"aggregate_class":["Arable"]}).sum()

    pasture_to_agricultural = total_forestable_pasture_land / (total_forestable_arable_land + total_forestable_pasture_land)

    forestable_pasture_ratio = total_forestable_pasture_land / total_uk_land
    forestable_arable_ratio = total_forestable_arable_land / total_uk_land

    to_forest_pasture = pctg.where(alc_mask, other=0).sel({"aggregate_class":["Improved grassland", "Semi-natural grassland"]})
    to_forest_arable = pctg.where(alc_mask, other=0).sel({"aggregate_class":"Arable"})

    # Spare the specified land type
    if forest_fraction >= 0:
        delta_forest_pasture = to_forest_pasture * forest_fraction / forestable_pasture_ratio
        
    else:
        delta_forest_pasture = to_forest_pasture * forest_fraction / forestable_pasture_ratio * pasture_to_agricultural
        delta_forest_arable = to_forest_arable * forest_fraction / forestable_arable_ratio * (1-pasture_to_agricultural)
        pctg.loc[{"aggregate_class":"Arable"}] -= delta_forest_arable
        pctg.loc[{"aggregate_class":"Broadleaf woodland"}] += delta_forest_arable*bdleaf_conif_ratio
        pctg.loc[{"aggregate_class":"Coniferous woodland"}] += delta_forest_arable*(1-bdleaf_conif_ratio)
        
    pctg.loc[{"aggregate_class":["Improved grassland", "Semi-natural grassland"]}] -= delta_forest_pasture
    pctg.loc[{"aggregate_class":"Broadleaf woodland"}] += delta_forest_pasture.sum(dim="aggregate_class")*bdleaf_conif_ratio
    pctg.loc[{"aggregate_class":"Coniferous woodland"}] += delta_forest_pasture.sum(dim="aggregate_class")*(1-bdleaf_conif_ratio)

    # Add spared class to the land use map
    datablock["land"]["percentage_land_use"] = pctg

    # Scale food production and imports
    new_use_pasture = pctg.sel({"aggregate_class":["Improved grassland", "Semi-natural grassland"]}).sum()
    new_use_arable = pctg.sel({"aggregate_class":"Arable"}).sum()
    
    scale_use_pasture = (new_use_pasture/old_use_pasture).to_numpy()
    scale_use_arable = (new_use_arable/old_use_arable).to_numpy()

    food_orig = datablock["food"]["g/cap/day"]
    scale_forest_pasture = logistic_food_supply(food_orig, timescale, 1, scale_use_pasture)
    scale_forest_arable = logistic_food_supply(food_orig, timescale, 1, scale_use_arable)

    scaled_items_pasture = food_orig.sel(Item=food_orig.Item_origin=="Animal Products").Item.values
    scaled_items_arable = food_orig.sel(Item=food_orig.Item_origin=="Vegetal Products").Item.values

    out = food_orig.fbs.scale_add(element_in="production",
                                  element_out="imports",
                                  scale=scale_forest_pasture,
                                  items=scaled_items_pasture,
                                  add=False)
    
    out = out.fbs.scale_add(element_in="production",
                                  element_out="imports",
                                  scale=scale_forest_arable,
                                  items=scaled_items_arable,
                                  add=False)
    
    out = check_negative_source(out, "production")
    out = check_negative_source(out, "imports")

    ratio = out / food_orig
    ratio = ratio.where(~np.isnan(ratio), 1)

    # Update per cap/day values and per year values using the same ratio, which
    # is independent of population growth
    qty_key = ["g/cap/day", "g_prot/cap/day", "g_fat/cap/day", "kCal/cap/day"]
    for key in qty_key:
        datablock["food"][key] *= ratio

    # datablock["food"]["g/cap/day"] = out

    return datablock

def peatland_restoration(datablock, restore_fraction, new_land_type, old_land_type, items,
                         peat_map_key=None, mask_val=None):
    """Replaces a specified land type fraction and sets it to a new type called
    'peatland'. Scales food production and imports to reflect the change in land
    use.
    """
        
    timescale = datablock["global_parameters"]["timescale"]
    
    pctg = datablock["land"]["percentage_land_use"].copy(deep=True)
    old_use = datablock["land"]["percentage_land_use"].sel({"aggregate_class":old_land_type}).sum()

    if peat_map_key is not None:
        peat_map_da = datablock["land"][peat_map_key]
    
        if mask_val is not None:
            peat_mask = np.isin(peat_map_da, mask_val)
    
    # if no mask is provided, then use the whole map
    else:
        peat_mask = np.ones_like(pctg, dtype=bool)

    to_spare = pctg.where(peat_mask, other=0).sel({"aggregate_class":old_land_type})

    # Spare the specified land type
    delta_spared =  to_spare * restore_fraction
    pctg.loc[{"aggregate_class":old_land_type}] -= delta_spared

    if new_land_type not in pctg.aggregate_class.values:
        spared_new_class = xr.zeros_like(pctg.isel(aggregate_class=0)).where(np.isfinite(pctg.isel(aggregate_class=0)))
        spared_new_class["aggregate_class"] = new_land_type
        pctg = xr.concat([pctg, spared_new_class], dim="aggregate_class")

    pctg.loc[{"aggregate_class":new_land_type}] += delta_spared.sum(dim="aggregate_class")

    # Add spared class to the land use map
    datablock["land"]["percentage_land_use"] = pctg

    # Scale food production and imports
    new_use = pctg.sel({"aggregate_class":old_land_type}).sum()
    scale_use = (new_use/old_use).to_numpy()

    food_orig = datablock["food"]["g/cap/day"]
    scale_spare = logistic_food_supply(food_orig, timescale, 1, scale_use)

    scaled_items = food_orig.sel(Item=food_orig.Item_origin==items).Item.values

    out = food_orig.fbs.scale_add(element_in="production",
                                  element_out="imports",
                                  scale=scale_spare,
                                  items=scaled_items,
                                  add=False)
    datablock["food"]["g/cap/day"] = out
    
    ratio = out / food_orig
    ratio = ratio.where(~np.isnan(ratio), 1)

    datablock["food"]["g/cap/day"] = out

    return datablock

def ccs_model(datablock, waste_BECCS, overseas_BECCS, DACCS, biochar):
    """Computes the CCS sequestration from the different sources
    
    Parameters
    ----------

    waste_BECCS : float
        Total maximum sequestration (in t CO2e / year) from food waste-origin BECCS
    overseas_BECCS : float
        Total maximum sequestration (in t CO2e / year) from overseas biomass BECCS
    DACCS : float
        Total maximum sequestration (in t CO2e / year) from DACCS
    biochar : float
        Total maximum sequestration (in t CO2e / year) from biochar and enhanced weathering
    """
    
    timescale = datablock["global_parameters"]["timescale"]
    food_orig = datablock["food"]["g/cap/day"]
    pctg = datablock["land"]["percentage_land_use"]

    # Compute the total area of BECCS land used in hectares, and the total
    # sequestration in Mt CO2e / year

    land_BECCS_area = pctg.sel({"aggregate_class":"BECCS"}).sum().to_numpy()
    land_BECCS = land_BECCS_area * datablock["beccs_crops_seq_ha_yr"]

    logistic_0_val = logistic_food_supply(food_orig, timescale, 0, 1)

    waste_BECCS_seq_array = waste_BECCS * logistic_0_val
    overseas_BECCS_seq_array = overseas_BECCS * logistic_0_val
    DACCS_seq_array = DACCS * logistic_0_val
    biochar_seq_array = biochar * logistic_0_val
    land_BECCS_seq_array = land_BECCS * logistic_0_val

    # Create a dataset with the different sequestration sources
    seq_ds = xr.Dataset({"BECCS from waste": waste_BECCS_seq_array,
                         "BECCS from overseas biomass": overseas_BECCS_seq_array,
                         "BECCS from land": land_BECCS_seq_array,
                         "DACCS": DACCS_seq_array,
                         "Biochar": biochar_seq_array})
    
    seq_da = seq_ds.to_array(dim="Item", name="sequestration")
    
    if "co2e_sequestration" not in datablock["impact"]:
        datablock["impact"]["co2e_sequestration"] = seq_da
    else:
        # append sequestration to existing sequestration da
        seq_da_in = datablock["impact"]["co2e_sequestration"]
        seq_da = xr.concat([seq_da_in, seq_da], dim="Item")
        datablock["impact"]["co2e_sequestration"] = seq_da

    # Compute the total cost of sequestration in pounds per year
    cost_BECCS_tCO2e = linear_scale(food_orig.Year.values[0],
                              2030,
                              2050,
                              food_orig.Year.values[-1],
                              c_init=123,
                              c_end=93)
    
    cost_DACCS_tCO2e = linear_scale(food_orig.Year.values[0],
                                    2030,
                                    2050,
                                    food_orig.Year.values[-1],
                                    c_init=245,
                                    c_end=180)

    cost_waste_BECCS = waste_BECCS_seq_array * cost_BECCS_tCO2e
    cost_overseas_BECCS = overseas_BECCS_seq_array * cost_BECCS_tCO2e
    cost_land_BECCS = land_BECCS_seq_array * cost_BECCS_tCO2e
    cost_DACCSS = DACCS_seq_array * cost_DACCS_tCO2e

    cost_CCS_ds = xr.Dataset({"BECCS from waste": cost_waste_BECCS,
                             "BECCS from overseas biomass": cost_overseas_BECCS,
                             "BECCS from land": cost_land_BECCS,
                             "DACCS": cost_DACCSS})
    
    cost_CCS_da = cost_CCS_ds.to_array(dim="Item", name="cost")
    datablock["impact"]["cost"] = cost_CCS_da

    return datablock    

def forest_sequestration_model(datablock, land_type, seq):
    """Computes total annual sequestration from the different sources"""
    
    if np.isscalar(land_type):
        land_type = [land_type]
    
    if np.isscalar(seq):
        seq = [seq]

    timescale = datablock["global_parameters"]["timescale"]
    food_orig = datablock["food"]["g/cap/day"]

    # Load the land use data from the datablock
    pctg = datablock["land"]["percentage_land_use"].copy(deep=True)
    logistic_0_val = logistic_food_supply(food_orig, timescale, 0, 1)

    for land_type_i, seq_i in zip(land_type, seq):

        # Compute forest area in ha, maximum anual sequestration, and growth curve
        area_land = pctg.loc[{"aggregate_class":land_type_i}].sum().to_numpy()
        max_seq = area_land * seq_i

    
        land_type_seq = max_seq * logistic_0_val

        # Create a dataset with the different sequestration sources
        seq_ds = xr.Dataset({land_type_i: land_type_seq})
        seq_da = seq_ds.to_array(dim="Item", name="sequestration")
    
        if "co2e_sequestration" not in datablock["impact"]:
            datablock["impact"]["co2e_sequestration"] = seq_da
        else:
            # append sequestration to existing sequestration da
            seq_da_in = datablock["impact"]["co2e_sequestration"]
            seq_da = xr.concat([seq_da_in, seq_da], dim="Item")
            datablock["impact"]["co2e_sequestration"] = seq_da

    # Compute agroecology sequestration

    return datablock

def scale_impact(datablock, scale_factor, items=None):
    """ Scales the impact values for the selected items relative to the
    the baseline impact factors.
    """

    timescale = datablock["global_parameters"]["timescale"]
    # load quantities and impacts
    food_orig = datablock["food"]["g/cap/day"]
    impacts = datablock["impact"]["gco2e/gfood"].copy(deep=True)
    impacts_baseline = datablock["impact"]["baseline"].copy(deep=True)

    items = get_items(food_orig, items)

    # scale the impacts using the baseline values as reference
    scale = logistic_food_supply(food_orig, timescale, 0, scale_factor)
    delta = impacts_baseline.loc[{"Item": items}] * scale
    impacts.loc[{"Item": items}] = impacts.loc[{"Item": items}] - delta
    datablock["impact"]["gco2e/gfood"] = impacts

    return datablock

def scale_production(datablock, scale_factor, items=None):
    """ Scales the production values for the selected items by multiplying them by
    a multiplicative factor.
    """

    timescale = datablock["global_parameters"]["timescale"]

    # load quantities and impacts
    food_orig = datablock["food"]["g/cap/day"].copy(deep=True)

    # if no items are specified, do nothing
    items = get_items(food_orig, items)

    scale_prod = logistic_food_supply(food_orig, timescale, 1, scale_factor)

    out = food_orig.fbs.scale_add(element_in="production",
                                element_out="imports",
                                scale=scale_prod,
                                items=items,
                                add=False)
    
    # Reduce feed and seed
    out = feed_scale(out, food_orig, source = "imports")

    out = check_negative_source(out, "production", "imports")
    out = check_negative_source(out, "imports", "exports", add=False)
    
    ratio = out / food_orig
    ratio = ratio.where(~np.isnan(ratio), 1)

    # Update per cap/day values and per year values using the same ratio, which
    # is independent of population growth
    # qty_key = ["g/cap/day", "g_prot/cap/day", "g_fat/cap/day", "kCal/cap/day"]
    # for key in qty_key:
    #     datablock["food"][key] *= ratio

    datablock["food"]["g/cap/day" ] *= ratio

    return datablock

def BECCS_farm_land(datablock, farm_percentage, items, land_type="Arable",
                    new_land_type="BECCS", mask_map=None, mask_values=None):
    """Repurposes farm land for BECCS, reducing the amount of food production,
    and increasing the amount of CO2e sequestered.
    """

    timescale = datablock["global_parameters"]["timescale"]
    pctg = datablock["land"]["percentage_land_use"].copy(deep=True)
    old_use = datablock["land"]["percentage_land_use"].sel({"aggregate_class":land_type}).sum()

    if mask_map is not None:
        mask_map = datablock["land"][mask_map].copy(deep=True)
    
    # if no alc grade is provided, then use the whole map
        if mask_values is not None:
            peat_mask = np.isin(mask_map, mask_values)
    
    else:
        peat_mask = np.ones_like(pctg, dtype=bool)

    to_spare = pctg.where(peat_mask, other=0).sel({"aggregate_class":land_type})

    # Spare the specified land type
    delta_spared =  to_spare * farm_percentage
    pctg.loc[{"aggregate_class":land_type}] -= delta_spared

    if new_land_type not in pctg.aggregate_class.values:
        spared_new_class = xr.zeros_like(pctg.isel(aggregate_class=0)).where(np.isfinite(pctg.isel(aggregate_class=0)))
        spared_new_class["aggregate_class"] = new_land_type
        pctg = xr.concat([pctg, spared_new_class], dim="aggregate_class")

    if "aggregate_class" in delta_spared.dims:
        pctg.loc[{"aggregate_class":new_land_type}] += delta_spared.sum(dim="aggregate_class")
    else:
        pctg.loc[{"aggregate_class":new_land_type}] += delta_spared

    # Add spared class to the land use map
    datablock["land"]["percentage_land_use"] = pctg

    # Scale food production and imports
    new_use = pctg.sel({"aggregate_class":land_type}).sum()
    scale_use = (new_use/old_use).fillna(1).to_numpy()

    food_orig = datablock["food"]["g/cap/day"]
    scale_spare = logistic_food_supply(food_orig, timescale, 1, scale_use)

    # scaled_items = food_orig.sel(Item=food_orig.Item_origin=="Vegetal Products").Item.values
    scaled_items = get_items(food_orig, items)

    out = food_orig.fbs.scale_add(element_in="production",
                                  element_out="imports",
                                  scale=scale_spare,
                                  items=scaled_items,
                                  add=False)
    
    ratio = out / food_orig
    ratio = ratio.where(~np.isnan(ratio), 1)
    datablock["food"]["g/cap/day"] = out

    return datablock

def agroecology_model(datablock, land_percentage, land_type, 
                      agroecology_class="Agroecology", tree_coverage=0.1,
                      replaced_items=None, new_items=None, item_yield=None,
                      seq_ha_yr=6.26):
    """Changes traditional agricultural land use to agroecological land use.
    
    Parameters
    ----------
    datablock : dict
        The datablock dictionary, containing all the model parameters and
        datasets.
    land_type : list
        The type or types of land that will be converted to agroecology.
    land_percentage : list
        The percentage or percentages of land that will be converted to
        agroecology.
    tree_coverage : float
        The percentage of each land class that will be converted to trees. This
        also sets the production value of the land class, via a 1-tree_coverage
        factor.
    replaced_items : list
        The items that will be replaced by agroecological products.
    new_items : list
        The additional items that will be grown in agroecological land.
    item_yield : float
        The yield of the additional agroecological products in g/ha/day.
    seq_ha_yr : float
        CO2e sequestration of agroecological land in t CO2e/ha/year.

    Returns
    -------
    datablock : dict
        The updated datablock dictionary, containing all the model parameters
        and datasets.
    """

    # Load land use and food data from datablock
    pctg = datablock["land"]["percentage_land_use"].copy(deep=True)
    food_orig = datablock["food"]["g/cap/day"].copy(deep=True)
    old_use = pctg.sel({"aggregate_class":land_type}).sum()
    alc = datablock["land"]["dominant_classification"]
    timescale = datablock["global_parameters"]["timescale"]

    # Compute land percentages to be converted to agroecology and remove them
    # from the land_type classes
    delta_agroecology = pctg.loc[{"aggregate_class":land_type}] * land_percentage
    pctg.loc[{"aggregate_class":land_type}] -= delta_agroecology

    # Add the agroecology percentage to the new agroecology class
    if agroecology_class not in pctg.aggregate_class.values:
        new_class = xr.zeros_like(pctg.isel(aggregate_class=0)).where(np.isfinite(pctg.isel(aggregate_class=0)))
        new_class["aggregate_class"] = agroecology_class
        pctg = xr.concat([pctg, new_class], dim="aggregate_class")

    delta_total = delta_agroecology.sum(dim="aggregate_class")
    pctg.loc[{"aggregate_class":agroecology_class}] += delta_total

    out = food_orig.copy(deep=True)

    # Reduce production of replaced items if they are provided
    if replaced_items is not None:
        new_use = pctg.sel({"aggregate_class":land_type}).sum()
        scale_use = (new_use/old_use) + (1-tree_coverage) * (1-new_use/old_use)
        scale_use = scale_use.to_numpy()

        scale_arr = logistic_food_supply(out, timescale, 1, scale_use)

        out = out.fbs.scale_add(element_in="production",
                                element_out="imports",
                                scale=scale_arr,
                                items=replaced_items,
                                add=False)
        
        out = check_negative_source(out, "production", "imports")
        out = check_negative_source(out, "imports", "production")

    # Add new items by scaling production from current values to future values
    if new_items is not None:
        if np.isscalar(new_items):
            new_items = [new_items]
        if np.isscalar(item_yield):
            item_yield = [item_yield]

        pop = datablock["population"]["population"].isel(Year=-1, Region=0)

        for item, yld in zip(new_items, item_yield):
            old_production = food_orig["production"].sel({"Item":item}).isel(Year=-1)
            new_production = old_production + yld * delta_agroecology.sum()/pop
            production_scale = (new_production / old_production).to_numpy()
            production_scale_array = logistic_food_supply(food_orig, timescale, 1, production_scale)

            out = out.fbs.scale_add(element_in="production",
                                element_out="imports",
                                scale=production_scale_array,
                                items=item,
                                add=False)
        
    # Compute forest area in ha, maximum anual sequestration, and growth curve
    area_agroecology = pctg.loc[{"aggregate_class":agroecology_class}].sum().to_numpy()
    max_seq_agroecology = area_agroecology * seq_ha_yr

    agroecology_seq = logistic_food_supply(food_orig, timescale, 1, c_end=max_seq_agroecology)
    
    # Create a dataset with the different sequestration sources
    seq_ds = xr.Dataset({agroecology_class: agroecology_seq})
    
    seq_da = seq_ds.to_array(dim="Item", name="sequestration")
    if "co2e_sequestration" not in datablock["impact"]:
        datablock["impact"]["co2e_sequestration"] = seq_da

    else:
        # append sequestration to existing sequestration da
        seq_da_in = datablock["impact"]["co2e_sequestration"]
        seq_da = xr.concat([seq_da_in, seq_da], dim="Item")
        datablock["impact"]["co2e_sequestration"] = seq_da

    # Rewrite land use data to datablock
    datablock["land"]["percentage_land_use"] = pctg

    ratio = out / food_orig
    ratio = ratio.where(~np.isnan(ratio), 1)

    # Update per cap/day values and per year values using the same ratio, which
    # is independent of population growth

    datablock["food"]["g/cap/day" ] *= ratio

    return datablock

def feed_scale(fbs, ref, elasticity=None, source="production"):
    """Scales the feed, seed and processing quantities according to the change
    in production of animal and vegetal products"""

    # Obtain reference production values
    ref_feed_arr = ref["production"].sel(Item=ref.Item_origin=="Animal Products").sum(dim="Item")
    ref_seed_arr = ref["production"].sel(Item=ref.Item_origin=="Vegetal Products").sum(dim="Item")
    
    # Compute scaling factors for feed and seed based on proportional production
    feed_scale = fbs["production"].sel(Item=fbs.Item_origin=="Animal Products").sum(dim="Item") \
                / ref_feed_arr
    seed_scale = fbs["production"].sel(Item=fbs.Item_origin=="Vegetal Products").sum(dim="Item") \
                / ref_seed_arr
    
    # Set feed_scale and seed_scale to 1 where ref arrays are close or equal to zero
    feed_scale = xr.where(np.isclose(ref_feed_arr, 0), 1, feed_scale)
    seed_scale = xr.where(np.isclose(ref_seed_arr, 0), 1, seed_scale)

    processing_scale = fbs["production"].sum(dim="Item") \
                / ref["production"].sum(dim="Item")

    if elasticity is not None:
        out = fbs.fbs.scale_add(element_in="feed", element_out=source,
                                scale=feed_scale, elasticity=elasticity)
        
        out = out.fbs.scale_add(element_in="seed",element_out=source,
                                scale=seed_scale, elasticity=elasticity)
        
        out = out.fbs.scale_add(element_in="processing",element_out=source,
                                scale=processing_scale, elasticity=elasticity)
    
    else:
        out = fbs.fbs.scale_add(element_in="feed", element_out=source,
                                scale=feed_scale)
        
        out = out.fbs.scale_add(element_in="seed",element_out=source,
                                scale=seed_scale)
        
        out = out.fbs.scale_add(element_in="processing",element_out=source,
                                scale=processing_scale)


    return out

def check_negative_source(fbs, source, fallback=None, add=True):
    """Checks for negative values in the source element and adds the difference
    to the fallback element"""

    if fallback is None:
        if source == "production":
            fallback = "imports"
        elif source == "imports":
            fallback = "production"
        elif source == "exports":
            fallback = "production"

    delta_neg = fbs[source].where(fbs[source] < 0, other=0)
    fbs[source] -= delta_neg

    if add:
        fbs[fallback] += delta_neg
    else:
        fbs[fallback] -= delta_neg

    return fbs

def logistic_food_supply(fbs, timescale, c_init, c_end):
    """Creates a logistic curve using the year range of the input food balance
    supply"""

    y0 = fbs.Year.values[0]
    y1 = 2021
    y2 = 2021 + timescale
    y3 = fbs.Year.values[-1]

    scale = logistic_scale(y0, y1, y2, y3, c_init=c_init, c_end=c_end)

    return scale

def scale_kcal_feed(obs, ref, items):
    """Scales the feed quantities according to the difference in production of 
    specified items, on a calorie by calorie basis"""

    # Obtain reference and observed production values
    ref_prod = ref["production"].sel(Item=items).expand_dims(dim="Item").sum(dim="Item")
    obs_prod = obs["production"].sel(Item=items).expand_dims(dim="Item").sum(dim="Item")

    # Compute difference 
    delta = obs_prod - ref_prod

    # Compute scaling factors for feed based on new required quantities
    ref_feed = ref["feed"].sum(dim="Item")
    obs_feed = obs["feed"].sum(dim="Item")

    # Scaling factor to be applied to old feed quantities
    feed_scale = (obs_feed + delta) / obs_feed

    # Adjust feed quantities
    out = obs.fbs.scale_add(element_in="feed",
                            element_out="production",
                            scale=feed_scale)
    
    return out

def production_land_scale(datablock, bdleaf_conif_ratio):
    """Scales land based on the relative production change of livestock and
    arable crops"""

    land = datablock["land"]["percentage_land_use"].copy(deep=True)
    obs = datablock["food"]["g/cap/day"].copy(deep=True)
    # print(obs)
    ref = datablock["food"]["baseline_projected"].copy(deep=True)

    # Obtain reference and observed production values
    ref_livest = ref["production"].sel(Year=2050, Item=ref.Item_origin=="Animal Products").sum(dim="Item")
    ref_arable = ref["production"].sel(Year=2050, Item=ref.Item_origin=="Vegetal Products").sum(dim="Item")

    obs_livest = obs["production"].sel(Year=2050, Item=obs.Item_origin=="Animal Products").sum(dim="Item")
    obs_arable = obs["production"].sel(Year=2050, Item=obs.Item_origin=="Vegetal Products").sum(dim="Item")

    # Compute ratios
    livest_ratio = obs_livest / ref_livest
    arable_ratio = obs_arable / ref_arable

    # Scale land use types
    delta_pasture = land.loc[{"aggregate_class":["Improved grassland", "Semi-natural grassland"]}] * (1-livest_ratio)
    land.loc[{"aggregate_class":["Improved grassland", "Semi-natural grassland"]}] -= delta_pasture

    delta_arable = land.loc[{"aggregate_class":"Arable"}] * (1-arable_ratio)
    land.loc[{"aggregate_class":"Arable"}] -= delta_arable

    # Remaining or excess land is allocated to or from forest
    # Calculate total percentage
    total = land.sum(dim="aggregate_class")
    
    # Check if total differs from 100
    delta = 100 - total
    delta = delta.where(np.isfinite(land.isel(aggregate_class=0)))

    # Adjust Broadleaf woodland to maintain 100% total
    if "Broadleaf woodland" in land.aggregate_class:
        land.loc[{"aggregate_class":"Broadleaf woodland"}] += delta*bdleaf_conif_ratio
    # If Broadleaf woodland doesn't exist, create it
    else:
        land.loc[{"aggregate_class":"Broadleaf woodland"}] = delta*bdleaf_conif_ratio

    # Adjust Coniforus woodland to maintain 100% total
    if "Coniferous woodland" in land.aggregate_class:
        land.loc[{"aggregate_class":"Coniferous woodland"}] += delta*(1-bdleaf_conif_ratio)
    # If Coniferous woodland doesn't exist, create it
    else:
        land.loc[{"aggregate_class":"Coniferous woodland"}] = delta*(1-bdleaf_conif_ratio)
    
    datablock["land"]["percentage_land_use"] = land

    return datablock

def managed_agricultural_land_carbon_model(datablock, fraction, managed_class,
                                           old_class):
    """Replaces a fraction of "arable" and "pasture" land types with "managed
    arable" and "managed pasture" respectively.
    """

    if np.isscalar(managed_class):
        managed_class = [managed_class]

    if np.isscalar(old_class):
        old_class = [old_class]    

    # Load land use data from datablock
    pctg = datablock["land"]["percentage_land_use"].copy(deep=True)

    # Create new category for "managed arable" land
    for new_class_name in managed_class:
        if new_class_name not in pctg.aggregate_class.values:
            _new_class = xr.zeros_like(pctg.isel(aggregate_class=0)).where(np.isfinite(pctg.isel(aggregate_class=0)))
            _new_class["aggregate_class"] = new_class_name
            pctg = xr.concat([pctg, _new_class], dim="aggregate_class")

    # # Compute arable fraction to be managed and remove from the arable
    # delta_arable = pctg.loc[{"aggregate_class":"Arable"}] * fraction
    # pctg.loc[{"aggregate_class":"Arable"}] -= delta_arable
    # pctg.loc[{"aggregate_class":"Managed arable"}] += delta_arable

    # Compute pasture fraction to be managed and remove from the pasture classes
    delta_arable = pctg.loc[{"aggregate_class":old_class}] * fraction
    pctg.loc[{"aggregate_class":old_class}] -= delta_arable
    pctg.loc[{"aggregate_class":managed_class}] += delta_arable.sum(dim="aggregate_class")

    # Rewrite land use data to datablock
    datablock["land"]["percentage_land_use"] = pctg
    return datablock

def zero_land_farming_model(datablock, fraction, items, land_type="Arable",
                            bdleaf_conif_ratio=0.5):
    """Reduces arable land proportional to the fraction of produced food
    assumed to be farmed in vertical / urban farms. Production remains constant.
    This ignores any item passed which is not a Vegetal Product.
    """

    food_orig = datablock["food"]["g/cap/day"].copy(deep=True)
    
    items = get_items(food_orig, items)

    timescale = datablock["global_parameters"]["timescale"]

    # Load land use data from datablock
    pctg = datablock["land"]["percentage_land_use"].copy(deep=True)

    # Load production data from datablock
    plant_items = food_orig.sel(Item=food_orig.Item_origin=="Vegetal Products").Item.values

    # Filter items to only include plant items
    items = [item for item in items if item in plant_items]

    # Create scaling array
    scale = logistic_food_supply(food_orig, timescale, 1, fraction)

    # Compute ratio of plant products now being produced in urban/vertical farms
    food_to_shift = food_orig["production"].sel(Item=items).sum(dim="Item") * scale

    shift_ratio_da =  food_to_shift / food_orig["production"].sel(Item=plant_items).sum(dim="Item")
    shift_ratio = shift_ratio_da.isel(Year=-1).values

    # Compute delta land use
    delta_arable = pctg.loc[{"aggregate_class":land_type}] * shift_ratio
    pctg.loc[{"aggregate_class":land_type}] -= delta_arable
    # Rewrite land use data to datablock

    # Add forested percentage to the land use map
    pctg.loc[{"aggregate_class":"Broadleaf woodland"}] += delta_arable * bdleaf_conif_ratio
    pctg.loc[{"aggregate_class":"Coniferous woodland"}] += delta_arable * (1-bdleaf_conif_ratio)

    datablock["land"]["percentage_land_use"] = pctg

    return datablock

def extra_urban_farming(datablock, fraction, items):
    """Increases production of certain items relative to the baseline production
    while keeping land utilization constant"""

    # Read datasets
    food_orig = datablock["food"]["g/cap/day"].copy(deep=True)
    food_base = datablock["food"]["baseline_projected"]
    
    # Read item lists
    items = get_items(food_orig, items)

    timescale = datablock["global_parameters"]["timescale"]

    # Create scaling array
    scale = logistic_food_supply(food_orig, timescale, 0, fraction)

    # Compute quantity of products now being produced in urban/vertical farms
    delta = food_base["production"].sel(Item=items) * scale
    delta = delta.fillna(0)

    # Add delta to productions and remove from imports
    food_orig["production"].loc[{"Item":items}] = food_orig["production"].loc[{"Item":items}] + delta
    food_orig["imports"].loc[{"Item":items}] = food_orig["imports"].loc[{"Item":items}] - delta

    # Check for negative sources and correct
    out = check_negative_source(food_orig, "imports", "exports", add=False)

    # Rewrite food data to datablock and return
    datablock["food"]["g/cap/day"] = out

    return datablock

def mixed_farming_model(datablock, fraction, prod_scale_factor, items,
                        secondary_items, secondary_prod_scale_factor,
                        land_type=["Arable",
                                   "Managed arable"],
                        secondary_land_type=["Improved grassland",
                                             "Semi-natural grassland",
                                             "Managed pasture"],
                        new_land_type="Mixed farming"):
    
    """Converts arable land to mixed farming.

    In mix farms, primary crops have a small decrease in production of specified
    items, set by primary_items and primary_production_scale.
    Secondary items are increased by a specified amount, set by secondary_items,
    secondary_production_scale and the current land used primarily for the secondary
    items.
    """

    # Load land use data from datablock
    old_land = datablock["land"]["percentage_land_use"]
    pctg = datablock["land"]["percentage_land_use"].copy(deep=True)
    food_orig = datablock["food"]["g/cap/day"].copy(deep=True)
    timescale = datablock["global_parameters"]["timescale"]

    # Create new category for "mixed farming" land
    if new_land_type not in pctg.aggregate_class.values:
        _new_class = xr.zeros_like(pctg.isel(aggregate_class=0)).where(np.isfinite(pctg.isel(aggregate_class=0)))
        _new_class["aggregate_class"] = new_land_type
        pctg = xr.concat([pctg, _new_class], dim="aggregate_class")

    # Compute arable fraction to be converted to mixed farming
    delta_arable = pctg.loc[{"aggregate_class":land_type}] * fraction
    pctg.loc[{"aggregate_class":land_type}] -= delta_arable
    pctg.loc[{"aggregate_class":new_land_type}] += delta_arable.sum(dim="aggregate_class")

    # Compute relative change in arable land
    mixed_farm_frac = delta_arable.sum() / old_land.loc[{"aggregate_class":land_type}].sum()
    arable_scale = 1 - mixed_farm_frac + mixed_farm_frac * prod_scale_factor
    arable_scale = arable_scale.values

    # Get items
    items = get_items(food_orig, items)
    secondary_items = get_items(food_orig, secondary_items)

    scale = logistic_food_supply(food_orig, timescale, 1, arable_scale)

    out = food_orig.fbs.scale_add(element_in="production",
                                  element_out="imports",
                                  scale=scale,
                                  items=items,
                                  add=False)
    
    # Compute relative change in secondary items
    # Get relative new area of mixed farming to secondary producing area
    total_area_secondary = pctg.loc[{"aggregate_class":secondary_land_type}].sum()
    mixed_farm_to_secondary_ratio = delta_arable.sum() / total_area_secondary
    secondary_ratio = 1 + mixed_farm_to_secondary_ratio * secondary_prod_scale_factor
    secondary_ratio = secondary_ratio.values

    secondary_scale = logistic_food_supply(food_orig, timescale, 1, secondary_ratio)

    out = out.fbs.scale_add(element_in="production",
                                  element_out="exports",
                                  scale=secondary_scale,
                                  items=secondary_items,
                                  add=True)
    

    # Update land use data to datablock
    datablock["land"]["percentage_land_use"] = pctg

    # Rewrite food data datablock
    datablock["food"]["g/cap/day"] = out

    return datablock

def get_items(fbs, items):
    """Get items from food data."""
    if isinstance(items, tuple):
        items = fbs.sel(Item=np.isin(fbs[items[0]], items[1])).Item.values
    elif np.isscalar(items):
        items = [items]
    return items

def shift_production(datablock, scale, items, items_target, land_area_ratio):
    
    """Scales production of selected items while adjusting target item list
    production according to the specified scaling factors to account for
    different yield rates.

    Parameters
    ----------
    datablock : dict
        The datablock dictionary, containing all the model parameters and
        datasets.
    scale : float
        The scale factor to be applied to the items being shifted.
    items : list
        The items to be shifted.
    items_target : list
        The items to which the production will be shifted.
    total_to_items : float
        Ratio of total productive land to land used for target items.
    target_to_items : float
        Ratio of total productive land of target items to productive land of
        items being scaled.
    """

    # Load food data from datablock
    food_orig = datablock["food"]["g/cap/day"].copy(deep=True)
    timescale = datablock["global_parameters"]["timescale"]

    items = get_items(food_orig, items)
    items_target = get_items(food_orig, items_target)

    # Create scaling array
    scale_items = logistic_food_supply(food_orig, timescale, 1, 1 + scale)

    scale_target = 1 - land_area_ratio * scale
    scale_target = logistic_food_supply(food_orig, timescale, 1, scale_target)

    # Scale production quantities

    out = food_orig.fbs.scale_add(element_in="production",
                                element_out="imports",
                                scale=scale_items,
                                items=items,
                                add=False)
    
    out = out.fbs.scale_add(element_in="production",
                            element_out="imports",
                            scale=scale_target,
                            items=items_target,
                            add=False)

    # Check for negative sources and correct
    out = check_negative_source(out, "imports", "exports", add=False)

    # Rewrite food data to datablock and return
    datablock["food"]["g/cap/day"] = out

    return datablock

def compute_metrics(datablock, sector_emissions_dict):
    """Computes a series of metrics from the resulting datablock"""

    datablock["metrics"] = {}

    # nutritional_values
    qty_keys = ["g_prot/cap/day", "g_fat/cap/day", "kCal/cap/day"]
    nutrition_keys = ["g_prot/g_food", "g_fat/g_food", "kCal/g_food"]

    for qk, nk in zip(qty_keys, nutrition_keys):
        datablock["food"][qk] = datablock["food"][nk] * datablock["food"]["g/cap/day"]

    # Emissions balance
    metric_yr = 2050
    reference_emissions_baseline = 94.24
    reference_emissions_baseline_agriculture = 53.69

    seq_da = datablock["impact"]["co2e_sequestration"].sel(Year=metric_yr)
    emissions = datablock["impact"]["g_co2e/year"]["production"].sel(Year=metric_yr)/1e6
    total_agriculture_emissions = emissions.sum(dim="Item").values/1e6
    total_seq = seq_da.sel(Item=["Broadleaf woodland",
                                 "Coniferous woodland",
                                 "New Broadleaf woodland",
                                 "New Coniferous woodland",
                                 "Managed pasture",
                                 "Managed arable",
                                 "Mixed farming",
                                 "Silvopasture",
                                 "Agroforestry"]).sum(dim="Item").values/1e6
    
    total_removals = seq_da.sel(Item=["BECCS from waste",
                                      "BECCS from overseas biomass",
                                      "BECCS from land",
                                      "DACCS",
                                      "Biochar"]).sum(dim="Item").values/1e6
    
    emissions_balance = xr.DataArray(data = list(sector_emissions_dict.values()),
                            name="Sectoral emissions",
                            coords={"Sector": list(sector_emissions_dict.keys())})
    
    emissions_balance.loc[{"Sector": "Agriculture"}] = total_agriculture_emissions
    emissions_balance.loc[{"Sector": "LU sinks"}] = -total_seq
    emissions_balance.loc[{"Sector": "Removals"}] = -total_removals

    emissions_balance.loc[{"Sector": "LU sources"}] -= seq_da.sel(Item=["Restored upland peat", "Restored lowland peat"]).sum(dim="Item").values/1e6
    total_emissions = emissions_balance.sum().values
    
    reducion_emissions_pctg = (total_emissions - reference_emissions_baseline) / reference_emissions_baseline * 100
    forest_sequestration_MtCO2 = seq_da.sel(Item=["Broadleaf woodland", "Coniferous woodland"]).sum(dim="Item").values/1e6
    agricultural_emissions = emissions_balance.sel(Sector="Agriculture").sum().values
    reduction_emissions_agricultural_pctg = (agricultural_emissions - reference_emissions_baseline_agriculture) / reference_emissions_baseline_agriculture * 100

    datablock["metrics"]["emissions_balance"] = emissions_balance
    datablock["metrics"]["total_sequestration"] = total_seq
    datablock["metrics"]["total_removals"] = total_removals
    datablock["metrics"]["total_emissions"] = total_emissions
    datablock["metrics"]["reference_emissions_baseline"] = reference_emissions_baseline
    datablock["metrics"]["reduction_emissions_pctg"] = reducion_emissions_pctg
    datablock["metrics"]["forest_sequestration_MtCO2"] = forest_sequestration_MtCO2
    datablock["metrics"]["agricultural_emissions"] = agricultural_emissions
    datablock["metrics"]["reduction_emissions_agricultural_pctg"] = reduction_emissions_agricultural_pctg

    # SSR
    # ssr_metric = st.session_state["ssr_metric"]
    ssr_metric_str = ["g/cap/day", "g_prot/cap/day", "g_fat/cap/day", "g_co2e/cap/day", "kCal/cap/day"]

    for ssr_metric in ssr_metric_str:
        gcapday = datablock["food"][ssr_metric].sel(Year=metric_yr).fillna(0)
        gcapday = gcapday.fbs.group_sum(coordinate="Item_origin", new_name="Item")
        gcapday_ref = datablock["food"][ssr_metric].sel(Year=2020).fillna(0)
        gcapday_ref = gcapday_ref.fbs.group_sum(coordinate="Item_origin", new_name="Item")

        SSR_ref = gcapday_ref.fbs.SSR()
        SSR_metric_yr = gcapday.fbs.SSR()

        datablock["metrics"][ssr_metric + "SSR_ref"] = SSR_ref
        datablock["metrics"][ssr_metric + "SSR_metric_yr"] = SSR_metric_yr
        datablock["metrics"][ssr_metric + "gcapday_item_origin"] = gcapday
        datablock["metrics"][ssr_metric + "gcapday_ref_item_origin"] = gcapday_ref

    # Herd size

    # Read baseline herd sizes from session state
    baseline_beef_herd = datablock["baseline_beef_herd"]
    baseline_dairy_herd = datablock["baseline_dairy_herd"]
    dairy_herd_beef = datablock["dairy_herd_beef"]
    baseline_poultry_heads = datablock["baseline_poultry_heads"]
    baseline_pig_heads = datablock["baseline_pig_heads"]
    baseline_sheep_flock = datablock["baseline_sheep_flock"]
    baseline_dairy_herd_2y = datablock["baseline_dairy_herd_breeding_aged_2_years_"]

    # Read total population from datablock
    pop_baseline = datablock["population"]["population"].sel(Region = 826, Year=2020)
    pop_new = datablock["population"]["population"].sel(Region = 826)

    # Dairy herd

    baseline_dairy_production = pop_baseline * datablock["food"]["g/cap/day"]["production"].sel(Year=2020, Item=[2743, 2740, 2948]).fillna(0).sum()
    new_dairy_production = pop_new * datablock["food"]["g/cap/day"]["production"].sel(Item=[2743, 2740, 2948]).fillna(0).sum(dim="Item")
    new_dairy_herd = new_dairy_production / baseline_dairy_production * baseline_dairy_herd
    new_dairy_herd["Item"] = "Dairy herd"
    new_dairy_herd.name = "Dairy herd"
    new_dairy_herd_2y = new_dairy_production / baseline_dairy_production * baseline_dairy_herd_2y
    new_dairy_herd_2y["Item"] = "Dairy herd 2 years and older"
    new_dairy_herd_2y.name = "Dairy herd 2 years and older"

    datablock["metrics"]["baseline_dairy_herd"] = baseline_dairy_herd
    datablock["metrics"]["new_dairy_herd"] = new_dairy_herd
    datablock["metrics"]["new_dairy_herd_2y"] = new_dairy_herd_2y

    # Beef herd
    baseline_beef_production = pop_baseline * datablock["food"]["g/cap/day"]["production"].sel(Year=2020, Item=2731).fillna(0).sum()
    new_beef_production = pop_new * datablock["food"]["g/cap/day"]["production"].sel(Item=2731).fillna(0)
    new_beef_herd = baseline_beef_herd * (new_beef_production - dairy_herd_beef * baseline_beef_production * new_dairy_herd / baseline_dairy_herd) / ((1 - dairy_herd_beef)*baseline_beef_production)
    new_beef_herd["Item"] = "Beef herd"
    new_beef_herd.name = "Beed herd"

    datablock["metrics"]["baseline_beef_herd"] = baseline_beef_herd
    datablock["metrics"]["new_beef_herd"] = new_beef_herd
    datablock["metrics"]["new_herd"] = new_dairy_herd + new_beef_herd

    # Poultry, pigs and sheep
    baseline_poultry_production = pop_baseline * datablock["food"]["g/cap/day"]["production"].sel(Year=2020, Item=2734).fillna(0).sum()
    new_poultry_production = pop_new * datablock["food"]["g/cap/day"]["production"].sel(Item=2734).fillna(0)
    new_poultry_heads = baseline_poultry_heads * new_poultry_production / baseline_poultry_production
    new_poultry_heads["Item"] = "Poultry heads"
    new_poultry_heads.name = "Poultry heads"

    datablock["metrics"]["baseline_poultry_heads"] = baseline_poultry_heads
    datablock["metrics"]["new_poultry_heads"] = new_poultry_heads

    baseline_pig_production = pop_baseline * datablock["food"]["g/cap/day"]["production"].sel(Year=2020, Item=2733).fillna(0).sum()
    new_pig_production = pop_new * datablock["food"]["g/cap/day"]["production"].sel(Item=2733).fillna(0)
    new_pig_heads = baseline_pig_heads * new_pig_production / baseline_pig_production
    new_pig_heads["Item"] = "Pig heads"
    new_pig_heads.name = "Pig heads"

    datablock["metrics"]["baseline_pig_heads"] = baseline_pig_heads
    datablock["metrics"]["new_pig_heads"] = new_pig_heads

    baseline_sheep_production = pop_baseline * datablock["food"]["g/cap/day"]["production"].sel(Year=2020, Item=2732).fillna(0).sum()
    new_sheep_production = pop_new * datablock["food"]["g/cap/day"]["production"].sel(Item=2732).fillna(0)
    new_sheep_flock = baseline_sheep_flock * new_sheep_production / baseline_sheep_production
    new_sheep_flock["Item"] = "Sheep flock"
    new_sheep_flock.name = "Sheep flock"

    datablock["metrics"]["baseline_sheep_flock"] = baseline_sheep_flock
    datablock["metrics"]["new_sheep_flock"] = new_sheep_flock

    datablock["metrics"]["all_animals"] = new_dairy_herd + new_beef_herd + new_poultry_heads + new_pig_heads + new_sheep_flock

    size_dataarrays = [new_dairy_herd, new_dairy_herd_2y, new_beef_herd,
                       new_poultry_heads, new_pig_heads, new_sheep_flock]

    for da in size_dataarrays:
        if "Item" not in da.dims:
            da = da.expand_dims(dim="Item")
        
        # Add to datablock
        if "livestock" not in datablock["metrics"]:
            datablock["metrics"]["livestock"] = da
        else:
            datablock["metrics"]["livestock"] = xr.concat([datablock["metrics"]["livestock"], da], dim="Item")

    # Land use
    pctg = datablock["land"]["percentage_land_use"]
    totals = pctg.sum(dim=["x", "y"])

    total_pasture = totals.sel(aggregate_class=["Improved grassland",
                                                "Semi-natural grassland",
                                                "Managed pasture",
                                                "Silvopasture"]).sum().values
    
    baseline_pasture = datablock["land"]["baseline"].sel(aggregate_class=["Improved grassland",
                                                                          "Semi-natural grassland"]).sum().values

    total_forest = totals.sel(aggregate_class=["Broadleaf woodland",
                                               "Coniferous woodland",
                                               "New Broadleaf woodland",
                                               "New Coniferous woodland"]).sum().values
    
    new_forest_land = (total_forest - datablock["land"]["baseline"].sel(aggregate_class=["Broadleaf woodland", "Coniferous woodland"]).sum().values)
    
    baseline_forest = datablock["land"]["baseline"].sel(aggregate_class=["Broadleaf woodland",
                                                                         "Coniferous woodland"]).sum().values

    total_arable = totals.sel(aggregate_class=["Arable",
                                               "Managed arable",
                                               "Mixed farming",
                                               "Agroforestry"]).sum().values
    
    baseline_arable = datablock["land"]["baseline"].sel(aggregate_class=["Arable"]).sum().values

    new_arable_land_pctg = (total_arable - baseline_arable) / baseline_arable * 100
    new_pasture_land_pctg = (total_pasture - baseline_pasture) / baseline_pasture * 100

    datablock["metrics"]["total_pasture"] = total_pasture
    datablock["metrics"]["total_forest"] = total_forest
    datablock["metrics"]["total_arable"] = total_arable
    datablock["metrics"]["baseline_pasture"] = baseline_pasture
    datablock["metrics"]["baseline_forest"] = baseline_forest
    datablock["metrics"]["baseline_arable"] = baseline_arable
    datablock["metrics"]["new_forest_land"] = new_forest_land
    datablock["metrics"]["new_arable_land_pctg"] = new_arable_land_pctg
    datablock["metrics"]["new_pasture_land_pctg"] = new_pasture_land_pctg

    # Crop sizes

    gcapday = datablock["food"]["g/cap/day"]["production"]

    baseline_potatoes_area_mha = 0.012
    baseline_potato_production = pop_baseline * gcapday.sel(Year=2020, Item=2531).fillna(0).sum().values
    new_potato_production = pop_new * gcapday.sel(Year=metric_yr, Item=2531).fillna(0).sum().values
    new_potato_area = baseline_potatoes_area_mha * new_potato_production / baseline_potato_production
    datablock["metrics"]["new_potato_area"] = new_potato_area
    

    baseline_oilseed_area_mha = 0.418
    baseline_oilseed_production = pop_baseline * gcapday.sel(Year=2020, Item=[2570, 2572, 2573, 2575, 2576, 2577, 2578, 2579, 2581, 2582, 2586 ]).fillna(0).sum().values
    new_oilseed_production = pop_new * gcapday.sel(Year=metric_yr, Item=[2570, 2572, 2573, 2575, 2576, 2577, 2578, 2579, 2581, 2582, 2586 ]).fillna(0).sum().values
    new_oilseed_area = baseline_oilseed_area_mha * new_oilseed_production / baseline_oilseed_production
    datablock["metrics"]["new_oilseed_area"] = new_oilseed_area

    baseline_cereal_area_mha = 3.1
    baseline_cereal_production = pop_baseline * gcapday.sel(Year=2020, Item=gcapday.Item_group=="Cereals - Excluding Beer").fillna(0).sum().values
    new_cereal_production = pop_new * gcapday.sel(Year=metric_yr, Item=gcapday.Item_group=="Cereals - Excluding Beer").fillna(0).sum().values
    
    new_cereal_area = baseline_cereal_area_mha * new_cereal_production / baseline_cereal_production
    datablock["metrics"]["new_cereal_area"] = new_cereal_area
    
    baseline_horticulture_area_mha = 0.145
    new_horiticulture_area = baseline_horticulture_area_mha * total_arable / baseline_arable * (1+datablock["horticulture"]/100)
    datablock["metrics"]["new_horticulture_area"] = new_horiticulture_area

    other_crops_area_mha = total_arable/1e6 - new_potato_area - new_oilseed_area - new_cereal_area - new_horiticulture_area
    datablock["metrics"]["other_crops_area_mha"] = other_crops_area_mha

    # Food balance sheet

    population = datablock["population"]["population"].sel(Region=826)
    food_qty = datablock["food"]["g/cap/day"]

    datablock["food"]["kton/year"] = food_qty * population / 1e6 * 365.25

    return datablock

def label_new_forest(datablock):

    land = datablock["land"]["percentage_land_use"].copy(deep=True)
    land_baseline = datablock["land"]["baseline"].copy(deep=True)

    if "New Broadleaf woodland" not in land.aggregate_class.values:
        new_class = xr.zeros_like(land.isel(aggregate_class=0)).where(np.isfinite(land.isel(aggregate_class=0)))
        new_class["aggregate_class"] = "New Broadleaf woodland"
        land = xr.concat([land.isel(aggregate_class=slice(0, 2)), new_class, land.isel(aggregate_class=slice(2, None))], dim="aggregate_class")
    
    if "New Coniferous woodland" not in land.aggregate_class.values:
        new_class = xr.zeros_like(land.isel(aggregate_class=0)).where(np.isfinite(land.isel(aggregate_class=0)))
        new_class["aggregate_class"] = "New Coniferous woodland"
        land = xr.concat([land.isel(aggregate_class=slice(0, 3)), new_class, land.isel(aggregate_class=slice(3, None))], dim="aggregate_class")

    for w_type in ["Broadleaf woodland", "Coniferous woodland"]:
        # Compute the difference between current and baseline woodland
        delta_w = land.sel(aggregate_class=w_type) - land_baseline.sel(aggregate_class=w_type)

        # Identify where the difference is positive (indicating new woodland)
        new_w_mask = delta_w > 0

        # Assign the positive difference to "New Broadleaf woodland"
        land.loc[{"aggregate_class": "New "+w_type}] += delta_w.where(new_w_mask, 0)

        # Limit "Broadleaf woodland" to the baseline model
        land.loc[{"aggregate_class": w_type}] = land_baseline.sel(aggregate_class=w_type).where(new_w_mask, land.sel(aggregate_class=w_type))

    datablock["land"]["percentage_land_use"] = land

    return datablock