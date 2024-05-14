#%%
import os
import pandas as pd
import ipeadatapy as ipea
import rpy2.robjects as robjects
from rpy2.robjects import pandas2ri
from rpy2.robjects.conversion import localconverter
import logging
from typing import List, Optional
import matplotlib.pyplot as plt
import seaborn as sns

# Set error capture logging
logging.basicConfig(level=logging.ERROR)

class DataProcessor:
    def __init__(self, bronze_folder: str, silver_folder: str, gold_folder: str, descriptive_analysis_folder: str):
        self.bronze_folder = bronze_folder
        self.silver_folder = silver_folder
        self.gold_folder = gold_folder
        self.descriptive_analysis_folder = descriptive_analysis_folder
        self.join_list = []

    def create_folders(self) -> None:
        folders = [self.bronze_folder, self.silver_folder, self.gold_folder, self.descriptive_analysis_folder]
        for folder in folders:
            os.makedirs(folder, exist_ok=True)

    def bronze_fetch(self
                   , series: str
                   , year: int
                   , filename: str
                   , r_code: Optional[str] = None) -> Optional[pd.DataFrame]:
        """
        Fetches IPEA raw data based on the series and year provided; 
        ipeadatar and ipeadatapy (ipea.territories(), ipea.timeseries()).

    Args:
        series (str): Series ID at IPEA database to fetch data.
        year (int): Year filter for the data fetched.
        filename (str): Filename to save the data fetched.
        r_code (Optional[str]): R code to ipeadatar for fetching data that isn't at ipeadatapy.

    Returns:
        DataFrame: Fetched data as pandas DataFrame, then saving at Bronze layer, or None if an error occurs (with an error log).
        """
        try:
            if filename == 'IDHM_2010.csv':
                data = robjects.r(r_code)
                with localconverter(robjects.default_converter + pandas2ri.converter) as cv:
                    raw_data = cv.rpy2py(data)
                if 'date' in raw_data.columns and raw_data['date'].dtype == 'float64':
                    raw_data['date'] = pd.to_datetime(raw_data['date']
                                                    , unit='D'
                                                    , origin='1970-01-01')
                raw_data = pd.DataFrame(raw_data)
            elif series == 'Municípios':
                raw_data = ipea.territories()
                raw_data = pd.DataFrame(raw_data)
            else:
                raw_data = ipea.timeseries(series=series
                                         , year=year)
                raw_data = pd.DataFrame(raw_data)

            self.saving_step(raw_data
                           , self.bronze_folder
                           , filename)
            return raw_data
        except Exception as e:
            logging.error(f"Error fetching data for {filename}: {e}")
            return None

    def saving_step(self
                  , data: pd.DataFrame
                  , folder: str
                  , filename: str) -> None:
        """
        Save data at respective layer with specified filename; 
        Bronze, Silver and Gold layers.

    Args:
        data (DataFrame): Fetched data at step before.
        folder (str): Directory emulating Medallion layer.
        filename (str): Filename to save the data fetched.

    Returns:
        File: Saved file at specified directory.
        """
        path = os.path.join(folder
                          , filename)
        data.to_csv(path
                  , index=False
                  , encoding='utf-8')

    def silver_transform(self
                       , df: pd.DataFrame
                       , filename: str) -> Optional[pd.DataFrame]:
        """
        Process IPEA Bronze data fetched to get it ready to consolidate; 
        Removing unused data, Relabeling fields, Row filtering, based on the series singularities.

    Args:
        df (DataFrame): DataFrame from Series ID fetched at IPEA.
        filename (str): Filename to save the data fetched.

    Returns:
        DataFrame: Processed data as pandas DataFrame, then saving at Silver layer, or None if an error occurs (with an error log).
        """
        try:
            if 'IDHM_2010.csv' in filename:
                df = df.query('(uname == "Municipality") & (date == "2010-01-01")') \
                       .drop(columns=['code'
                                    , 'uname'
                                    , 'date'])
                df = df.rename(columns={'tcode': 'CodMunIBGE'
                                      , 'value': 'IDHM 2010'})
            elif filename == 'Municípios.csv':
                df = df.query('LEVEL == "Municípios"') \
                       .drop(columns=['LEVEL'
                                    , 'AREA'
                                    , 'CAPITAL']) \
                       .rename(columns={'NAME': 'Município'
                                      , 'ID': 'CodMunIBGE'})
            else:
                df = df.query('NIVNOME == "Municípios"') \
                       .drop(columns=['CODE'
                                    , 'RAW DATE'
                                    , 'YEAR'
                                    , 'NIVNOME'])
                if 'PIB_2010.csv' in filename:
                    df['VALUE (R$ (mil), a preços do ano 2010)'] = df['VALUE (R$ (mil), a preços do ano 2010)'].astype(float) \
                                                                                                               .round(3) * 1000
                    df = df.rename(columns={'TERCODIGO': 'CodMunIBGE'
                                          , 'VALUE (R$ (mil), a preços do ano 2010)': 'PIB 2010 (R$)'})
                    df['PIB 2010 (R$)'] = pd.to_numeric(df['PIB 2010 (R$)'], errors='coerce')
                elif 'Arrecadação_2010.csv' in filename:
                    df = df.rename(columns={'TERCODIGO': 'CodMunIBGE'
                                          , 'VALUE (R$)': 'Receitas Correntes 2010 (R$)'})
                    df['Receitas Correntes 2010 (R$)'] = pd.to_numeric(df['Receitas Correntes 2010 (R$)'], errors='coerce')
                elif 'População_2010.csv' in filename:
                    df = df.rename(columns={'TERCODIGO': 'CodMunIBGE'
                                          , 'VALUE (Habitante)': 'Habitantes 2010'})
                    df = df.astype({'Habitantes 2010': int
                                  , 'CodMunIBGE': str}
                                  , errors='ignore')

            self.saving_step(df
                           , self.silver_folder
                           , filename)
            return df
        except Exception as e:
            logging.error(f"Error transforming data for {filename}: {e}")
            return None

    def gold_finish(self
                  , filename: str) -> pd.DataFrame:
        """
        Reunite IPEA Silver data processed to get it ready to use; 
        Merging variables, Reordering fields, N/A Row filtering, Sorting.

    Args:
        df (DataFrame): DataFrame from Series ID fetched at IPEA.
        filename (str): Filename to save the data fetched.

    Returns:
        DataFrame: Processed data as a single pandas DataFrame, then saving at Gold layer, or None if an error occurs.
        """
        merged_df = self.join_list[0]
        for df in self.join_list[1:]:
            df['CodMunIBGE'] = df['CodMunIBGE'].astype(str)
            merged_df = merged_df.merge(df
                                      , how='left'
                                      , on='CodMunIBGE')
        order_set = ['CodMunIBGE'
                   , 'Município'
                   , 'Habitantes 2010'
                   , 'IDHM 2010'
                   , 'Receitas Correntes 2010 (R$)'
                   , 'PIB 2010 (R$)'
                   , 'Carga Tributária Municipal 2010']
        merged_df.dropna(inplace=True)
        merged_df = merged_df.reindex(columns=order_set)
        merged_df.sort_values(by='CodMunIBGE'
                            , inplace=True)
        merged_df['Carga Tributária Municipal 2010'] = merged_df['Receitas Correntes 2010 (R$)'].div(merged_df['PIB 2010 (R$)'], fill_value=0).astype(float)

        # Perform descriptive analysis
        summary = merged_df.describe()
        print("Descriptive Statistics:\n", summary)
        summary.to_csv(os.path.join(self.descriptive_analysis_folder, 'descriptive_statistics.csv'))

        # Plot histograms using the colorblind friendly "viridis" palette
        sns.set_palette("viridis")

        # Calculate the 95th percentile for each column
        pib_95th = merged_df['PIB 2010 (R$)'].quantile(0.95)
        receitas_95th = merged_df['Receitas Correntes 2010 (R$)'].quantile(0.95)

        # Function to plot histogram with optional percentile adjustment and save as PDF
        def plot_chart(column, title, xlabel, percentile=None, filename=None):
            if percentile is not None:
                data = merged_df[merged_df[column] <= percentile][column]
            else:
                data = merged_df[column]
            plt.figure(figsize=(10, 6))
            sns.histplot(data, bins=100, kde=True)
            plt.title(title)
            plt.xlabel(xlabel)
            plt.ylabel('Frequency')
            plt.savefig(os.path.join(self.descriptive_analysis_folder, f'{filename}.pdf'))
            plt.close()

        # Assuming 'merged_df' is your DataFrame containing the data
        sns.set_theme(style="whitegrid")  # Set the style of the plot
        plt.figure(figsize=(10, 6))  # Set the size of the plot

        # Create the scatter plot
        sns.scatterplot(data=merged_df, x='Carga Tributária Municipal 2010', y='IDHM 2010')

        plt.title('Scatter Plot of IDHM 2010 vs Carga Tributária Municipal 2010')
        plt.xlabel('Carga Tributária Municipal 2010')
        plt.ylabel('IDHM 2010')
       # Save the scatter plot as a PDF in the descriptive analysis folder
        plt.savefig(os.path.join(self.descriptive_analysis_folder, 'Dispersao IDHM x Carga Tributaria.pdf'))

        # Plot histograms for each variable and save as PDF
        plot_chart('IDHM 2010', 'Distribution of IDHM 2010', 'IDHM 2010', filename='Histogram IDHM 2010')
        plot_chart('Carga Tributária Municipal 2010', 'Distribution of Carga Tributária Municipal 2010', 'Carga Tributária Municipal 2010', filename='Histogram Carga Tributaria Municipal 2010')
        plot_chart('PIB 2010 (R$)', 'Distribution of PIB 2010 (R$) - Adjusted for Outliers', 'PIB 2010 (R$)', pib_95th, filename='Histogram PIB 2010 adjusted')
        plot_chart('Receitas Correntes 2010 (R$)', 'Distribution of Receitas Correntes 2010 (R$) - Adjusted for Outliers', 'Receitas Correntes 2010 (R$)', receitas_95th, filename='Histogram Receitas Correntes 2010 adjusted')

        self.saving_step(merged_df
                       , self.gold_folder
                       , filename)
        return merged_df

    def process_data(self
                   , series: str
                   , year: int
                   , filename: str
                   , r_code: Optional[str] = None) -> None:
        """
        Setting workflow parameters to fetch, process and save data; 
        Fetching data parameters, directories and consolidation before Gold layer.

    Args:
        series (str): Series ID at IPEA database to fetch data.
        year (int): Year filter for the data fetched.
        filename (str): Filename to save the data fetched.
        r_code (Optional[str]): R code to ipeadatar for fetching data that isn't at ipeadatapy.

    Returns:
        DataFrame: If there is no Data, do bronze_fetch,
        if bronze_fetch is done, and silver_transform isn't, do silver_transform,
        if silver_transform is done, prepare pandas DataFrame to be processed at gold_finish.
        """
        bronze_df = self.bronze_fetch(series
                                    , year
                                    , filename
                                    , r_code)
        if bronze_df is not None:
            silver_df = self.silver_transform(bronze_df
                                            , filename)
            if silver_df is not None:
                self.join_list.append(silver_df)

def main():
    config = {
        'bronze': os.getenv('BRONZE_FOLDER', 'bronze'),
        'silver': os.getenv('SILVER_FOLDER', 'silver'),
        'gold': os.getenv('GOLD_FOLDER', 'gold'),
        'descriptive_analysis': os.getenv('DESCRIPTIVE_ANALYSIS_FOLDER', 'descriptive_analysis')
    }
    processor = DataProcessor(config['bronze'], config['silver'], config['gold'], config['descriptive_analysis'])
    processor.create_folders()

    data_series = [
        ('PIB_IBGE_5938_37', 2010, 'PIB_2010.csv'),
        ('RECORRM', 2010, 'Arrecadação_2010.csv'),
        ('POPTOT', 2010, 'População_2010.csv'),
        ('Municípios', None, 'Municípios.csv')
    ]
    for series, year, filename in data_series:
        processor.process_data(series
                             , year
                             , filename)

    r_code = """
    install.packages('ipeadatar', repos='http://cran.r-project.org')
    library(ipeadatar)
    data_IDHM <- ipeadatar::ipeadata(code = 'ADH_IDHM')
    data_IDHM
    """
    processor.process_data(None
                         , None
                         , 'IDHM_2010.csv'
                         , r_code=r_code)

    if processor.join_list:
        clean_data = processor.gold_finish('CleanData.csv')
        clean_data

if __name__ == "__main__":
    main()
# %%