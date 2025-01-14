# Stat_Canada.py (flowsa)
# !/usr/bin/env python3
# coding=utf-8
'''

Labour force characteristics by industry, annual (x 1,000)
How to cite: Statistics Canada. Table 14-10-0023-01 Labour force characteristics by industry, annual (x 1,000)
DOI: https://doi.org/10.25318/1410002301-eng
'''

import pandas as pd
import io
import zipfile
import pycountry
from flowsa.common import *


def sc_lfs_call(url, sc_response, args):
    """
    Convert response for calling url to pandas dataframe, begin parsing df into FBA format
    :param url: string, url
    :param sc_response: df, response from url call
    :param args: dictionary, arguments specified when running
    flowbyactivity.py ('year' and 'source')
    :return: pandas dataframe of original source data
    """
    # Convert response to dataframe
    # read all files in the stat canada zip
    with zipfile.ZipFile(io.BytesIO(sc_response.content), "r") as f:
        # read in file names
        for name in f.namelist():
            # if filename does not contain "MetaData", then create dataframe
            if "MetaData" not in name:
                data = f.open(name)
                df = pd.read_csv(data, header=0)
    return df


def sc_lfs_parse(dataframe_list, args):
    """
    Functions to being parsing and formatting data into flowbyactivity format
    :param dataframe_list: list of dataframes to concat and format
    :param args: arguments as specified in flowbyactivity.py ('year' and 'source')
    :return: dataframe parsed and partially formatted to flowbyactivity specifications
    """
    # concat dataframes
    df = pd.concat(dataframe_list, sort=False)
    # drop columns
    df = df.drop(columns=['COORDINATE', 'DECIMALS', 'DGUID', 'SYMBOL', 'TERMINATED', 'UOM_ID', 'SCALAR_ID', 'VECTOR'])
    # rename columns
    df = df.rename(columns={'GEO': 'Location',
                            'North American Industry Classification System (NAICS)': 'Description',
                            'REF_DATE': 'Year',
                            'STATUS': 'Spread',
                            'VALUE': "FlowAmount",
                            'Labour force characteristics': 'FlowName'})
    # limit data to pull
    df = df[df['Location'] == 'Canada']
    df = df[df['Sex'] == 'Both sexes']
    df = df[df['Age group'] == '15 years and over']
    df = df[df['FlowName'] == 'Employment'].reset_index(drop=True)
    df.loc[:, 'FlowAmount'] = df['FlowAmount'] * 10000
    df.loc[:, 'Unit'] = 'p'
    df = df.drop(columns=['Sex', 'Age group', 'UOM', 'SCALAR_FACTOR'])
    # extract NAICS as activity column. rename activity based on flowname
    df['Activity'] = df['Description'].str.extract('.*\[(.*)\].*')
    df.loc[df['Description'] == 'Total, all industries', 'Activity'] = '31-33'  # todo: change these activity names
    df.loc[df['Description'] == 'Other manufacturing industries', 'Activity'] = 'Other'
    df['FlowName'] = df['FlowName'].str.strip()
    df.loc[df['FlowName'] == 'Water intake', 'ActivityConsumedBy'] = df['Activity']
    df.loc[df['FlowName'].isin(['Water discharge', "Water recirculation"]), 'ActivityProducedBy'] = df['Activity']
    # drop columns used to create unit and activity columns
    df = df.drop(columns=['Activity'])
    # Modify the assigned RSD letter values to numeric value
    df.loc[df['Spread'] == 'A', 'Spread'] = 2.5  # given range: 0.01 - 4.99%
    df.loc[df['Spread'] == 'B', 'Spread'] = 7.5  # given range: 5 - 9.99%
    df.loc[df['Spread'] == 'C', 'Spread'] = 12.5  # given range: 10 - 14.99%
    df.loc[df['Spread'] == 'D', 'Spread'] = 20  # given range: 15 - 24.99%
    df.loc[df['Spread'] == 'E', 'Spread'] = 37.5  # given range:25 - 49.99%
    df.loc[df['Spread'] == 'F', 'Spread'] = 75  # given range: > 49.99%
    df.loc[df['Spread'] == 'x', 'Spread'] = withdrawn_keyword
    # hard code data
    df['Class'] = 'Employment'
    df['SourceName'] = 'StatCan_LFS'
    # temp hardcode canada iso code
    df['Location'] = call_country_code('Canada')
    df['LocationSystem'] = "ISO"
    df["MeasureofSpread"] = 'RSD'
    df["DataReliability"] = '3'
    df["DataCollection"] = '4'
    return df


def call_country_code(country):
    """

    :param country:
    :return:
    """
    """use pycountry to call on 3 digit iso country code"""
    country_info = pycountry.countries.get(name=country)
    country_numeric_iso = country_info.numeric
    return country_numeric_iso
