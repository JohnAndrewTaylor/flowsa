# flowbyactivity.py (flowsa)
# !/usr/bin/env python3
# coding=utf-8
"""
Methods for pulling data from http sources
File configuration requires a year for the data pull and a data source (yaml file name) as parameters
EX: --year 2015 --source USGS_NWIS_WU
"""

import argparse
from flowsa.common import *
from esupy.processed_data_mgmt import write_df_to_file
from flowsa.dataclean import clean_df
from flowsa.data_source_scripts.BEA import *
from flowsa.data_source_scripts.Blackhurst_IO import *
from flowsa.data_source_scripts.BLS_QCEW import *
from flowsa.data_source_scripts.CalRecycle_WasteCharacterization import *
from flowsa.data_source_scripts.Census_CBP import *
from flowsa.data_source_scripts.Census_AHS import *
from flowsa.data_source_scripts.Census_PEP_Population import *
from flowsa.data_source_scripts.EIA_CBECS_Water import *
from flowsa.data_source_scripts.EPA_NEI import *
from flowsa.data_source_scripts.NOAA_FisheryLandings import *
from flowsa.data_source_scripts.StatCan_GDP import *
from flowsa.data_source_scripts.StatCan_IWS_MI import *
from flowsa.data_source_scripts.StatCan_LFS import *
from flowsa.data_source_scripts.USDA_CoA_Cropland import *
from flowsa.data_source_scripts.USDA_CoA_Cropland_NAICS import *
from flowsa.data_source_scripts.USDA_CoA_Livestock import *
from flowsa.data_source_scripts.USDA_ERS_FIWS import *
from flowsa.data_source_scripts.USDA_IWMS import *
from flowsa.data_source_scripts.USGS_NWIS_WU import *
from flowsa.data_source_scripts.USDA_ERS_MLU import *
from flowsa.data_source_scripts.EIA_CBECS_Land import *
from flowsa.data_source_scripts.EIA_CBECS_Water import *
from flowsa.data_source_scripts.EIA_MECS import *
from flowsa.data_source_scripts.BLM_PLS import *
from flowsa.data_source_scripts.EIA_MER import *
from flowsa.data_source_scripts.EPA_GHGI import *
from flowsa.data_source_scripts.USGS_MYB_SodaAsh import *
from flowsa.data_source_scripts.USGS_WU_Coef import *


def parse_args():
    """Make year and source script parameters"""
    ap = argparse.ArgumentParser()
    ap.add_argument("-y", "--year", required=True, help="Year for data pull and save")
    ap.add_argument("-s", "--source", required=True, help="Data source code to pull and save")
    args = vars(ap.parse_args())
    return args


def set_fba_name(datasource,year):
    if year is not None:
        name_data = datasource + "_" + str(year)
    else:
        name_data = datasource
    return name_data


def build_url_for_query(config,args):
    """Creates a base url which requires string substitutions that depend on data source"""
    # if there are url parameters defined in the yaml, then build a url, else use "base_url"
    urlinfo = config["url"]
    if urlinfo != 'None':
        if 'url_params' in urlinfo:
            params = ""
            for k, v in urlinfo['url_params'].items():
                params = params+'&'+k+"="+str(v)

        if 'url_params' in urlinfo:
            build_url = "{0}{1}{2}".format(urlinfo['base_url'], urlinfo['api_path'], params)
        else:
            build_url = "{0}".format(urlinfo['base_url'])

        # substitute year from arguments and users api key into the url
        if "__year__" in build_url:
            build_url = build_url.replace("__year__", str(args["year"]))
        if "__apiKey__" in build_url:
            userAPIKey = load_api_key(config['api_name'])  # (common.py fxn)
            build_url = build_url.replace("__apiKey__", userAPIKey)
        return build_url


def assemble_urls_for_query(build_url, config, args):
    """Calls on helper functions defined in source.py files to replace parts of the url string"""
    if "url_replace_fxn" in config:
        if hasattr(sys.modules[__name__], config["url_replace_fxn"]):
            urls = getattr(sys.modules[__name__], config["url_replace_fxn"])(build_url, config, args)
    else:
        urls = []
        urls.append(build_url)
    return urls


def call_urls(url_list, args, config):
    """This method calls all the urls that have been generated.
    It then calls the processing method to begin processing the returned data. The processing method is specific to
    the data source, so this function relies on a function in source.py"""
    data_frames_list = []
    if url_list[0] is not None:
        for url in url_list:
            log.info("Calling " + url)
            r = make_http_request(url)
            if hasattr(sys.modules[__name__], config["call_response_fxn"]):
                df = getattr(sys.modules[__name__], config["call_response_fxn"])(url, r, args)
            if isinstance(df, pd.DataFrame):
                data_frames_list.append(df)
            elif isinstance(df, list):
                data_frames_list.extend(df)

    return data_frames_list


def parse_data(dataframe_list, args, config):
    """Calls on functions defined in source.py files, as parsing rules are specific to the data source."""
    if hasattr(sys.modules[__name__], config["parse_response_fxn"]):
        df = getattr(sys.modules[__name__], config["parse_response_fxn"])(dataframe_list, args)
        return df


def process_data_frame(df, source, year):
    """
    Process the given dataframe, cleaning, converting data, and writing the final parquet.

    This method was written to move code into a shared method, which was necessary to support
    the processing of a list of dataframes instead of a single dataframe.
    """
    # log that data was retrieved
    log.info("Retrieved data for " + source + ' ' + year)
    # add any missing columns of data and cast to appropriate data type
    log.info("Add any missing columns and check field datatypes")
    flow_df = clean_df(df, flow_by_activity_fields, fba_fill_na_dict, drop_description=False)
    # modify flow units
    flow_df = convert_fba_unit(flow_df)
    # sort df and reset index
    flow_df = flow_df.sort_values(['Class', 'Location', 'ActivityProducedBy', 'ActivityConsumedBy',
                                   'FlowName', 'Compartment']).reset_index(drop=True)
    # save as parquet file
    name_data = set_fba_name(source, year)
    meta = set_fb_meta(name_data, "FlowByActivity")
    write_df_to_file(flow_df,paths,meta)
    log.info("FBA generated and saved for " + name_data)


def main(**kwargs):
    # assign arguments
    if len(kwargs)==0:
        kwargs = parse_args()

    # assign yaml parameters (common.py fxn)
    config = load_sourceconfig(kwargs['source'])
    # update the local config with today's date
    config['date_generated']= pd.to_datetime('today').strftime('%Y-%m-%d')
    # update the method yaml with date generated
    update_fba_yaml_date(kwargs['source'])

    log.info("Creating dataframe list")
    # @@@01082021JS - Range of years defined, to support split into multiple Parquets:
    if '-' in str(kwargs['year']):
        years = str(kwargs['year']).split('-')
        min_year = int(years[0])
        max_year = int(years[1]) + 1
        year_iter = list(range(min_year, max_year))
    else:
        # Else only a single year defined, create an array of one:
        year_iter = [kwargs['year']]

    for p_year in year_iter:
        kwargs['year'] = str(p_year)
        # build the base url with strings that will be replaced
        build_url = build_url_for_query(config, kwargs)
        # replace parts of urls with specific instructions from source.py
        urls = assemble_urls_for_query(build_url, config, kwargs)
        # create a list with data from all source urls
        dataframe_list = call_urls(urls, kwargs, config)
        # concat the dataframes and parse data with specific instructions from source.py
        log.info("Concat dataframe list and parse data")
        df = parse_data(dataframe_list, kwargs, config)
        if isinstance(df, list):
            for frame in df:
                if not len(frame.index) == 0:
                    try:
                        source_names = frame['SourceName']
                        source_name = source_names.iloc[0]
                    except KeyError as err:
                        source_name = kwargs['source']
                    process_data_frame(frame, source_name, kwargs['year'])
        else:
            process_data_frame(df, kwargs['source'], kwargs['year'])

if __name__ == '__main__':
    main()
