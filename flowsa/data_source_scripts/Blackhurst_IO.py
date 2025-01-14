# Blackhurst_IO.py (scripts)
# !/usr/bin/env python3
# coding=utf-8
"""
Scrapes data from Blackhurst paper 'Direct and Indirect Water Withdrawals
for US Industrial Sectors' (Supplemental info)
Includes supporting functions for Blackhurst paper data.
"""

import io
import tabula
import pandas as pd
import numpy as np
import flowsa
from flowsa.common import US_FIPS, flow_by_activity_fields, fba_fill_na_dict
from flowsa.flowbyfunctions import assign_fips_location_system, \
    proportional_allocation_by_location_and_activity, filter_by_geoscale
from flowsa.dataclean import harmonize_units, clean_df
from flowsa.mapping import add_sectors_to_flowbyactivity
from flowsa.data_source_scripts.BLS_QCEW import clean_bls_qcew_fba


# Read pdf into list of DataFrame
def bh_call(url, bh_response, args):
    """
    Load the data in pdf format and produce a response
    :param url: url where data found
    :param bh_response: response to reading url
    :param args: arguments as specified in flowbyactivity.py ('year' and 'source')
    :return: dataframe to format into a flowbyactivty data set
    """

    pages = range(5, 13)
    bh_df_list = []
    for x in pages:
        bh_df = tabula.read_pdf(io.BytesIO(bh_response.content), pages=x, stream=True)[0]
        bh_df_list.append(bh_df)

    bh_df = pd.concat(bh_df_list, sort=False)

    return bh_df


def bh_parse(dataframe_list, args):
    """
    Functions to being parsing and formatting data into flowbyactivity format
    :param dataframe_list: list of dataframes to concat and format
    :param args: arguments as specified in flowbyactivity.py ('year' and 'source')
    :return: dataframe parsed and partially formatted to flowbyactivity specifications
    """
    # concat list of dataframes (info on each page)
    df = pd.concat(dataframe_list, sort=False)
    df = df.rename(columns={"I-O code": "ActivityConsumedBy",
                            "I-O description": "Description",
                            "gal/$M": "FlowAmount",
                            })
    # hardcode
    df.loc[:, 'FlowAmount'] = df['FlowAmount'] / 1000000  # original data in gal/million usd
    df['Unit'] = 'gal/USD'
    df['SourceName'] = 'Blackhurst_IO'
    df['Class'] = 'Water'
    df['FlowName'] = 'Water Withdrawals IO Vector'
    df['Location'] = US_FIPS
    df = assign_fips_location_system(df, '2002')
    df['Year'] = '2002'

    return df


def convert_blackhurst_data_to_gal_per_year(df, attr):
    """
    Load BEA Make After Redefinition data to convert dataframe units
    to gallon per year
    :param df: fba ataframe
    :param attr: attribute data from method yaml
    :return: transformed fba df
    """
    # import flowsa
    # from flowsa.common import fba_fill_na_dict
    # from flowsa.dataclean import harmonize_units
    # from flowsa.dataclean import clean_df

    # load the bea make table
    bmt = flowsa.getFlowByActivity(datasource='BEA_Make_AR',
                                   year=2002, flowclass='Money')
    # clean df
    bmt = clean_df(bmt, flow_by_activity_fields, fba_fill_na_dict)
    bmt = harmonize_units(bmt)
    # drop rows with flowamount = 0
    bmt = bmt[bmt['FlowAmount'] != 0]

    bh_df_revised = pd.merge(df, bmt[['FlowAmount', 'ActivityProducedBy', 'Location']],
                             left_on=['ActivityConsumedBy', 'Location'],
                             right_on=['ActivityProducedBy', 'Location']
                             )

    bh_df_revised.loc[:, 'FlowAmount'] = ((bh_df_revised['FlowAmount_x']) *
                                          (bh_df_revised['FlowAmount_y']))
    bh_df_revised.loc[:, 'Unit'] = 'gal'
    # drop columns
    bh_df_revised = bh_df_revised.drop(columns=["FlowAmount_x", "FlowAmount_y",
                                                'ActivityProducedBy_y'])
    bh_df_revised = bh_df_revised.rename(columns={"ActivityProducedBy_x":
                                                      "ActivityProducedBy"})

    return bh_df_revised


def convert_blackhurst_data_to_gal_per_employee(df_wsec, attr, method):
    """
    Load BLS employment data and use to transporm original units to gallons per employee
    :param df_wsec: dataframe that includes sector columns
    :param attr: attribute data from fba method yaml
    :param method: The name of the fba method yaml
    :return: transformed fba dataframe with sector columns
    """

    # import flowsa
    # from flowsa.mapping import add_sectors_to_flowbyactivity
    # from flowsa.flowbyfunctions import filter_by_geoscale
    # from flowsa.common import fba_fill_na_dict
    # from flowsa.dataclean import harmonize_units
    # from flowsa.dataclean import clean_df
    # from flowsa.data_source_scripts.BLS_QCEW import clean_bls_qcew_fba

    bls = flowsa.getFlowByActivity(datasource='BLS_QCEW', year=2002, flowclass='Employment')

    bls = filter_by_geoscale(bls, 'national')

    # clean df
    bls = clean_df(bls, flow_by_activity_fields, fba_fill_na_dict)
    bls = harmonize_units(bls)
    bls = clean_bls_qcew_fba(bls, attr=attr)

    # assign naics to allocation dataset
    bls_wsec = add_sectors_to_flowbyactivity(bls, sectorsourcename=method['target_sector_source'])
    # drop rows where sector = None ( does not occur with mining)
    bls_wsec = bls_wsec[~bls_wsec['SectorProducedBy'].isnull()]
    bls_wsec = bls_wsec.rename(columns={'SectorProducedBy': 'Sector',
                                        'FlowAmount': 'HelperFlow'})

    # merge the two dfs
    df = pd.merge(df_wsec,
                  bls_wsec[['Location', 'Sector', 'HelperFlow']],
                  how='left',
                  left_on=['Location', 'SectorConsumedBy'],
                  right_on=['Location', 'Sector'])
    # drop any rows where sector is None
    df = df[~df['Sector'].isnull()]
    # fill helperflow values with 0
    df['HelperFlow'] = df['HelperFlow'].fillna(0)

    # calculate proportional ratios
    df_wratio = proportional_allocation_by_location_and_activity(df, 'Sector')

    df_wratio = df_wratio.rename(columns={'FlowAmountRatio': 'EmployeeRatio',
                                          'HelperFlow': 'Employees'})

    # drop rows where helperflow = 0
    df_wratio = df_wratio[df_wratio['Employees'] != 0]

    # calculate gal/employee in 2002
    df_wratio.loc[:, 'FlowAmount'] = (df_wratio['FlowAmount'] *
                                      df_wratio['EmployeeRatio']) / df_wratio['Employees']
    df_wratio.loc[:, 'Unit'] = 'gal/employee'

    # drop cols
    df_wratio = df_wratio.drop(columns=['Sector', 'Employees', 'EmployeeRatio'])

    return df_wratio


def scale_blackhurst_results_to_usgs_values(df_to_scale, attr):
    """
    Scale the initial estimates for Blackhurst-based mining estimates to USGS values.
    Oil-based sectors are allocated a larger percentage of the difference between initial
    water withdrawal estimates and published USGS values.

    This method is based off the Water Satellite Table created by Yang and Ingwersen, 2017
    :param df_to_scale: fba dataframe to be modified
    :param attr: attributes from the fba method yaml
    :return: scaled fba results
    """

    # import flowsa
    # from flowsa.dataclean import harmonize_units

    # determine national level published withdrawal data for usgs mining in FBS method year
    pv_load = flowsa.getFlowByActivity(datasource="USGS_NWIS_WU",
                                       year=str(attr['helper_source_year']),
                                       flowclass='Water'
                                       )
    pv_load = harmonize_units(pv_load)
    pv_sub = pv_load[(pv_load['Location'] == str(US_FIPS)) &
                     (pv_load['ActivityConsumedBy'] == 'Mining')].reset_index(drop=True)
    pv = pv_sub['FlowAmount'].loc[0] * 1000000  # usgs unit is Mgal, blackhurst unit is gal

    # sum quantity of water withdrawals already allocated to sectors
    av = df_to_scale['FlowAmount'].sum()

    # calculate the difference between published value and allocated value
    vd = pv - av

    # subset df to scale into oil and non-oil sectors
    df_to_scale['sector_label'] = np.where(
        df_to_scale['SectorConsumedBy'].apply(lambda x: x[0:5] == '21111'), 'oil','nonoil')
    df_to_scale['ratio'] = np.where(df_to_scale['sector_label'] == 'oil', 2 / 3, 1 / 3)
    df_to_scale['label_sum'] = df_to_scale.groupby(['Location',
                                                    'sector_label'])['FlowAmount'].transform('sum')
    df_to_scale.loc[:, 'value_difference'] = vd.astype(float)

    # calculate revised water withdrawal allocation
    df_scaled = df_to_scale.copy()
    df_scaled.loc[:, 'FlowAmount'] = df_scaled['FlowAmount'] + \
                                     (df_scaled['FlowAmount'] / df_scaled['label_sum']) * \
                                     (df_scaled['ratio'] * df_scaled['value_difference'])
    df_scaled = df_scaled.drop(columns=['sector_label', 'ratio', 'label_sum', 'value_difference'])

    return df_scaled
