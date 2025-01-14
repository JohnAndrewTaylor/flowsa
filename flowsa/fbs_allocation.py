# fbs_allocation.py (flowsa)
# !/usr/bin/env python3
# coding=utf-8
"""
Functions to allocate data using additional data sources
"""

import sys
import logging as log
import numpy as np
import pandas as pd
import flowsa
from flowsa.common import load_source_catalog, activity_fields, US_FIPS, \
    fba_activity_fields, fbs_activity_fields, \
    fba_mapped_default_grouping_fields, flow_by_activity_fields, fba_fill_na_dict
from flowsa.datachecks import check_if_losing_sector_data, check_allocation_ratios, \
    check_if_location_systems_match
from flowsa.flowbyfunctions import collapse_activity_fields, \
    sector_aggregation, sector_disaggregation, allocate_by_sector, \
    proportional_allocation_by_location_and_activity, subset_df_by_geoscale
from flowsa.mapping import get_fba_allocation_subset, add_sectors_to_flowbyactivity
from flowsa.dataclean import replace_strings_with_NoneType, clean_df, harmonize_units
from flowsa.datachecks import check_if_data_exists_at_geoscale

# import specific functions
from flowsa.data_source_scripts.BEA import subset_BEA_Use
from flowsa.data_source_scripts.Blackhurst_IO import convert_blackhurst_data_to_gal_per_year, \
    convert_blackhurst_data_to_gal_per_employee, scale_blackhurst_results_to_usgs_values
from flowsa.data_source_scripts.BLS_QCEW import clean_bls_qcew_fba, \
    clean_bls_qcew_fba_for_employment_sat_table, \
    bls_clean_allocation_fba_w_sec
from flowsa.data_source_scripts.EIA_CBECS_Land import cbecs_land_fba_cleanup
from flowsa.data_source_scripts.EIA_MECS import mecs_energy_fba_cleanup, \
    eia_mecs_energy_clean_allocation_fba_w_sec, \
    mecs_land_fba_cleanup, mecs_land_fba_cleanup_for_land_2012_fbs, \
    mecs_land_clean_allocation_mapped_fba_w_sec
from flowsa.data_source_scripts.EPA_NEI import clean_NEI_fba, clean_NEI_fba_no_pesticides
from flowsa.data_source_scripts.StatCan_IWS_MI import convert_statcan_data_to_US_water_use
from flowsa.data_source_scripts.stewiFBS import stewicombo_to_sector, stewi_to_sector
from flowsa.data_source_scripts.USDA_CoA_Cropland import disaggregate_coa_cropland_to_6_digit_naics, \
    coa_irrigated_cropland_fba_cleanup, coa_nonirrigated_cropland_fba_cleanup
from flowsa.data_source_scripts.USDA_CoA_Cropland_NAICS import coa_cropland_naics_fba_wsec_cleanup
from flowsa.data_source_scripts.USDA_ERS_MLU import allocate_usda_ers_mlu_land_in_urban_areas,\
    allocate_usda_ers_mlu_other_land,\
    allocate_usda_ers_mlu_land_in_rural_transportation_areas
from flowsa.data_source_scripts.USDA_IWMS import disaggregate_iwms_to_6_digit_naics
from flowsa.data_source_scripts.USGS_NWIS_WU import usgs_fba_data_cleanup,\
    usgs_fba_w_sectors_data_cleanup


def direct_allocation_method(flow_subset_mapped, k, names, method):
    """
    Directly assign activities to sectors
    :param flow_subset_mapped: df, FBA with flows converted using fedelemflowlist
    :param k: str, source name
    :param names: list, activity names in activity set
    :param method: dictionary, FBS method yaml
    :return:
    """
    log.info('Directly assigning activities to sectors')
    fbs = flow_subset_mapped.copy()
    # for each activity, if activities are not sector like, check that there is no data loss
    if load_source_catalog()[k]['sector-like_activities'] is False:
        activity_list = []
        for n in names:
            log.debug('Checking for ' + n + ' at ' + method['target_sector_level'])
            fbs_subset = fbs[((fbs[fba_activity_fields[0]] == n) &
                              (fbs[fba_activity_fields[1]] == n)) |
                             (fbs[fba_activity_fields[0]] == n) |
                             (fbs[fba_activity_fields[1]] == n)].reset_index(drop=True)
            fbs_subset = check_if_losing_sector_data(fbs_subset, method['target_sector_level'])
            activity_list.append(fbs_subset)
        fbs = pd.concat(activity_list, ignore_index=True)
    return fbs


def function_allocation_method(flow_subset_mapped, names, attr, fbs_list):
    """

    :param flow_subset_mapped: df, FBA with flows converted using fedelemflowlist
    :param names: list, activity names in activity set
    :param attr:
    :param fbs_list:
    :return:
    """
    log.info('Calling on function specified in method yaml to allocate ' +
             ', '.join(map(str, names)) + ' to sectors')
    fbs = getattr(sys.modules[__name__],
                  attr['allocation_source'])(flow_subset_mapped, attr, fbs_list)
    return fbs


def dataset_allocation_method(flow_subset_mapped, attr, names, method,
                              k, v, aset, method_name, aset_names):
    """
    Method of allocation using a specified data source
    :param flow_subset_mapped: FBA subset mapped using federal elementary flow list
    :param attr: method attributes
    :param names:
    :param method:
    :param k:
    :param v:
    :param aset:
    :param method_name:
    :param aset_names:
    :return:
    """
    # add parameters to dictionary if exist in method yaml
    fba_dict = {}
    if 'allocation_flow' in attr:
        fba_dict['flowname_subset'] = attr['allocation_flow']
    if 'allocation_compartment' in attr:
        fba_dict['compartment_subset'] = attr['allocation_compartment']
    if 'clean_allocation_fba' in attr:
        fba_dict['clean_fba'] = attr['clean_allocation_fba']
    if 'clean_allocation_fba_w_sec' in attr:
        fba_dict['clean_fba_w_sec'] = attr['clean_allocation_fba_w_sec']

    # load the allocation FBA
    fba_allocation_wsec = load_map_clean_fba(method, attr, fba_sourcename=attr['allocation_source'],
                                             df_year=attr['allocation_source_year'],
                                             flowclass=attr['allocation_source_class'],
                                             geoscale_from=attr['allocation_from_scale'],
                                             geoscale_to=v['geoscale_to_use'], **fba_dict)

    # subset fba datasets to only keep the sectors associated with activity subset
    log.info("Subsetting " + attr['allocation_source'] + " for sectors in " + k)
    fba_allocation_subset = get_fba_allocation_subset(fba_allocation_wsec, k, names,
                                                      flowSubsetMapped=flow_subset_mapped,
                                                      allocMethod=attr['allocation_method'])

    # if there is an allocation helper dataset, modify allocation df
    if attr['allocation_helper'] == 'yes':
        log.info("Using the specified allocation help for subset of " + attr['allocation_source'])
        fba_allocation_subset = allocation_helper(fba_allocation_subset, attr, method, v)

    # create flow allocation ratios for each activity
    # if load_source_catalog()[k]['sector-like_activities']
    flow_alloc_list = []
    group_cols = fba_mapped_default_grouping_fields
    group_cols = [e for e in group_cols if e not in ('ActivityProducedBy', 'ActivityConsumedBy')]
    for n in names:
        log.debug("Creating allocation ratios for " + n)
        fba_allocation_subset_2 = get_fba_allocation_subset(fba_allocation_subset, k, [n],
                                                            flowSubsetMapped=flow_subset_mapped,
                                                            allocMethod=attr['allocation_method'],
                                                            activity_set_names=aset_names)
        if len(fba_allocation_subset_2) == 0:
            log.info("No data found to allocate " + n)
        else:
            flow_alloc = allocate_by_sector(fba_allocation_subset_2, k, attr['allocation_source'],
                                            attr['allocation_method'], group_cols,
                                            flowSubsetMapped=flow_subset_mapped)
            flow_alloc = flow_alloc.assign(FBA_Activity=n)
            flow_alloc_list.append(flow_alloc)
    flow_allocation = pd.concat(flow_alloc_list, ignore_index=True)

    # generalize activity field names to enable link to main fba source
    log.info("Generalizing activity columns in subset of " + attr['allocation_source'])
    flow_allocation = collapse_activity_fields(flow_allocation)

    # check for issues with allocation ratios
    check_allocation_ratios(flow_allocation, aset, k, method_name)

    # create list of sectors in the flow allocation df, drop any rows of data in the flow df that \
    # aren't in list
    sector_list = flow_allocation['Sector'].unique().tolist()

    # subset fba allocation table to the values in the activity list, based on overlapping sectors
    flow_subset_mapped = flow_subset_mapped.loc[
        (flow_subset_mapped[fbs_activity_fields[0]].isin(sector_list)) |
        (flow_subset_mapped[fbs_activity_fields[1]].isin(sector_list))]

    # check if fba and allocation dfs have the same LocationSystem
    log.info("Checking if flowbyactivity and allocation dataframes use the same location systems")
    check_if_location_systems_match(flow_subset_mapped, flow_allocation)

    # merge fba df w/flow allocation dataset
    log.info("Merge " + k + " and subset of " + attr['allocation_source'])
    for i, j in activity_fields.items():
        flow_subset_mapped = flow_subset_mapped.merge(
            flow_allocation[['Location', 'Sector', 'FlowAmountRatio', 'FBA_Activity']],
            left_on=['Location', j[1]["flowbysector"], j[0]["flowbyactivity"]],
            right_on=['Location', 'Sector', 'FBA_Activity'], how='left')

    # merge the flowamount columns
    flow_subset_mapped.loc[:, 'FlowAmountRatio'] =\
        flow_subset_mapped['FlowAmountRatio_x'].fillna(flow_subset_mapped['FlowAmountRatio_y'])
    # fill null rows with 0 because no allocation info
    flow_subset_mapped['FlowAmountRatio'] = flow_subset_mapped['FlowAmountRatio'].fillna(0)

    # check if fba and alloc dfs have data for same geoscales -
    # comment back in after address the 'todo'
    # log.info("Checking if flowbyactivity and allocation
    # dataframes have data at the same locations")
    # check_if_data_exists_for_same_geoscales(fbs, k, attr['names'])

    # drop rows where there is no allocation data
    fbs = flow_subset_mapped.dropna(subset=['Sector_x', 'Sector_y'], how='all').reset_index()

    # calculate flow amounts for each sector
    log.info("Calculating new flow amounts using flow ratios")
    fbs.loc[:, 'FlowAmount'] = fbs['FlowAmount'] * fbs['FlowAmountRatio']

    # drop columns
    log.info("Cleaning up new flow by sector")
    fbs = fbs.drop(columns=['Sector_x', 'FlowAmountRatio_x', 'Sector_y', 'FlowAmountRatio_y',
                            'FlowAmountRatio', 'FBA_Activity_x', 'FBA_Activity_y'])
    return fbs


def allocation_helper(df_w_sector, attr, method, v):
    """
    Used when two df required to create allocation ratio
    :param df_w_sector:
    :param method: currently written for 'multiplication' and 'proportional'
    :param attr:
    :return:
    """

    # add parameters to dictionary if exist in method yaml
    fba_dict = {}
    if 'helper_flow' in attr:
        fba_dict['flowname_subset'] = attr['helper_flow']
    if 'clean_helper_fba' in attr:
        fba_dict['clean_fba'] = attr['clean_helper_fba']
    if 'clean_helper_fba_wsec' in attr:
        fba_dict['clean_fba_w_sec'] = attr['clean_helper_fba_wsec']

    # load the allocation FBA
    helper_allocation = load_map_clean_fba(method, attr, fba_sourcename=attr['helper_source'],
                                           df_year=attr['helper_source_year'],
                                           flowclass=attr['helper_source_class'],
                                           geoscale_from=attr['helper_from_scale'],
                                           geoscale_to=v['geoscale_to_use'], **fba_dict)

    # run sector disagg to capture any missing lower level naics
    helper_allocation = sector_disaggregation(helper_allocation, fba_mapped_default_grouping_fields)

    # generalize activity field names to enable link to water withdrawal table
    helper_allocation = collapse_activity_fields(helper_allocation)
    # drop any rows not mapped
    helper_allocation = helper_allocation[helper_allocation['Sector'].notnull()]
    # drop columns
    helper_allocation = helper_allocation.drop(columns=['Activity', 'Min', 'Max'])

    # rename column
    helper_allocation = helper_allocation.rename(columns={"FlowAmount": 'HelperFlow'})

    # determine the df_w_sector column to merge on
    df_w_sector = replace_strings_with_NoneType(df_w_sector)
    sec_consumed_list = df_w_sector['SectorConsumedBy'].drop_duplicates().values.tolist()
    sec_produced_list = df_w_sector['SectorProducedBy'].drop_duplicates().values.tolist()
    # if a sector field column is not all 'none', that is the column to merge
    if all(v is None for v in sec_consumed_list):
        sector_col_to_merge = 'SectorProducedBy'
    elif all(v is None for v in sec_produced_list):
        sector_col_to_merge = 'SectorConsumedBy'
    else:
        log.error('There is not a clear sector column to base merge with helper allocation dataset')

    # merge allocation df with helper df based on sectors, depending on geo scales of dfs
    if (attr['helper_from_scale'] == 'state') and (attr['allocation_from_scale'] == 'county'):
        helper_allocation.loc[:, 'Location_tmp'] = \
            helper_allocation['Location'].apply(lambda x: x[0:2])
        df_w_sector.loc[:, 'Location_tmp'] = df_w_sector['Location'].apply(lambda x: x[0:2])
        # merge_columns.append('Location_tmp')
        modified_fba_allocation =\
            df_w_sector.merge(helper_allocation[['Location_tmp', 'Sector', 'HelperFlow']],
                              how='left',
                              left_on=['Location_tmp', sector_col_to_merge],
                              right_on=['Location_tmp', 'Sector'])
        modified_fba_allocation = modified_fba_allocation.drop(columns=['Location_tmp'])
    elif (attr['helper_from_scale'] == 'national') and \
            (attr['allocation_from_scale'] != 'national'):
        modified_fba_allocation = df_w_sector.merge(helper_allocation[['Sector', 'HelperFlow']],
                                                    how='left',
                                                    left_on=[sector_col_to_merge],
                                                    right_on=['Sector'])
    else:
        modified_fba_allocation =\
            df_w_sector.merge(helper_allocation[['Location', 'Sector', 'HelperFlow']],
                              left_on=['Location', sector_col_to_merge],
                              right_on=['Location', 'Sector'])

    # modify flow amounts using helper data
    if 'multiplication' in attr['helper_method']:
        # todo: modify so if missing data, replaced with
        #  value from one geoscale up instead of national
        # todo: modify year after merge if necessary
        # if missing values (na or 0), replace with national level values
        replacement_values =\
            helper_allocation[helper_allocation['Location'] ==
                              US_FIPS].reset_index(drop=True)
        replacement_values = replacement_values.rename(columns={"HelperFlow": 'ReplacementValue'})
        modified_fba_allocation = modified_fba_allocation.merge(
            replacement_values[['Sector', 'ReplacementValue']], how='left')
        modified_fba_allocation.loc[:, 'HelperFlow'] = modified_fba_allocation['HelperFlow'].fillna(
            modified_fba_allocation['ReplacementValue'])
        modified_fba_allocation.loc[:, 'HelperFlow'] =\
            np.where(modified_fba_allocation['HelperFlow'] == 0,
                     modified_fba_allocation['ReplacementValue'],
                     modified_fba_allocation['HelperFlow'])

        # replace non-existent helper flow values with a 0, so after multiplying,
        # don't have incorrect value associated with new unit
        modified_fba_allocation['HelperFlow'] =\
            modified_fba_allocation['HelperFlow'].fillna(value=0)
        modified_fba_allocation.loc[:, 'FlowAmount'] = modified_fba_allocation['FlowAmount'] * \
                                                       modified_fba_allocation['HelperFlow']
        # drop columns
        modified_fba_allocation =\
            modified_fba_allocation.drop(columns=["HelperFlow", 'ReplacementValue', 'Sector'])

    elif attr['helper_method'] == 'proportional':
        modified_fba_allocation =\
            proportional_allocation_by_location_and_activity(modified_fba_allocation,
                                                             sector_col_to_merge)
        modified_fba_allocation['FlowAmountRatio'] =\
            modified_fba_allocation['FlowAmountRatio'].fillna(0)
        modified_fba_allocation.loc[:, 'FlowAmount'] = modified_fba_allocation['FlowAmount'] * \
                                                       modified_fba_allocation['FlowAmountRatio']
        modified_fba_allocation =\
            modified_fba_allocation.drop(columns=['FlowAmountRatio', 'HelperFlow', 'Sector'])

    elif attr['helper_method'] == 'proportional-flagged':
        # calculate denominators based on activity and 'flagged' column
        modified_fba_allocation =\
            modified_fba_allocation.assign(Denominator=
                                           modified_fba_allocation.groupby(
                                               ['FlowName', 'ActivityConsumedBy', 'Location',
                                                'disaggregate_flag']
                                           )['HelperFlow'].transform('sum'))
        modified_fba_allocation = modified_fba_allocation.assign(
            FlowAmountRatio=modified_fba_allocation['HelperFlow'] /
                            modified_fba_allocation['Denominator'])
        modified_fba_allocation =\
            modified_fba_allocation.assign(FlowAmount=modified_fba_allocation['FlowAmount'] *
                                                      modified_fba_allocation['FlowAmountRatio'])
        modified_fba_allocation =\
            modified_fba_allocation.drop(columns=['disaggregate_flag', 'Sector', 'HelperFlow',
                                                  'Denominator', 'FlowAmountRatio'])
        # run sector aggregation
        modified_fba_allocation = sector_aggregation(modified_fba_allocation,
                                                     fba_mapped_default_grouping_fields)

    # drop rows of 0
    modified_fba_allocation =\
        modified_fba_allocation[modified_fba_allocation['FlowAmount'] != 0].reset_index(drop=True)

    # todo: change units
    modified_fba_allocation.loc[modified_fba_allocation['Unit'] == 'gal/employee', 'Unit'] = 'gal'

    # option to scale up fba values
    if 'scaled' in attr['helper_method']:
        log.info("Scaling " + attr['helper_source'] + ' to FBA values')
        # tmp hard coded - need to generalize
        if attr['helper_source'] == 'BLS_QCEW':
            modified_fba_allocation =\
                scale_blackhurst_results_to_usgs_values(modified_fba_allocation, attr)
            # modified_fba_allocation = getattr(sys.modules[__name__],
            # attr["scale_helper_results"])(modified_fba_allocation, attr)

    return modified_fba_allocation


def load_map_clean_fba(method, attr, fba_sourcename, df_year, flowclass,
                       geoscale_from, geoscale_to, **kwargs):
    """
    Load, clean, and map a FlowByActivity df
    :param method:
    :param attr:
    :param fba_sourcename:
    :param df_year:
    :param flowclass:
    :param geoscale_from:
    :param geoscale_to:
    :param kwargs:
    :return:
    """

    # from flowsa.datachecks import check_if_data_exists_at_geoscale
    # from flowsa.mapping import add_sectors_to_flowbyactivity

    log.info("Loading allocation flowbyactivity " + fba_sourcename + " for year " +
             str(df_year))
    fba = flowsa.getFlowByActivity(datasource=fba_sourcename, year=df_year, flowclass=flowclass)
    fba = clean_df(fba, flow_by_activity_fields, fba_fill_na_dict)
    fba = harmonize_units(fba)

    # check if allocation data exists at specified geoscale to use
    log.info("Checking if allocation data exists at the " + geoscale_from + " level")
    check_if_data_exists_at_geoscale(fba, geoscale_from)

    # aggregate geographically to the scale of the flowbyactivty source, if necessary
    fba = subset_df_by_geoscale(fba, geoscale_from, geoscale_to)

    # subset based on yaml settings
    if 'flowname_subset' in kwargs:
        if kwargs['flowname_subset'] != 'None':
            fba = fba.loc[fba['FlowName'].isin(kwargs['flowname_subset'])]
    if 'compartment_subset' in kwargs:
        if kwargs['compartment_subset'] != 'None':
            fba = fba.loc[fba['Compartment'].isin(kwargs['compartment_subset'])]

    # cleanup the fba allocation df, if necessary
    if 'clean_fba' in kwargs:
        log.info("Cleaning " + fba_sourcename)
        fba = getattr(sys.modules[__name__], kwargs["clean_fba"])(fba, attr=attr)
    # reset index
    fba = fba.reset_index(drop=True)

    # assign sector to allocation dataset
    log.info("Adding sectors to " + fba_sourcename)
    fba_wsec = add_sectors_to_flowbyactivity(fba, sectorsourcename=method['target_sector_source'])

    # call on fxn to further clean up/disaggregate the fba allocation data, if exists
    if 'clean_fba_w_sec' in kwargs:
        log.info("Further disaggregating sectors in " + fba_sourcename)
        fba_wsec = getattr(sys.modules[__name__], kwargs['clean_fba_w_sec'])(fba_wsec, attr=attr, method=method)

    return fba_wsec