import openpyxl
from bs4 import BeautifulSoup
import csv
from tqdm import tqdm


def _extract_unique_values_from_tsv(data_tsv_path: str,
                                    header_htm_path: str,
                                    data_dictionary_xls: dict,
                                    max_unique: int = 25,
                                    name_of_sheet_with_dictionary: str = 'Flatfile Formats'
                                    ) -> dict:
    """
    Extract unique values from a TSV file based on var_name defined in an HTM file, and enhance the unique values
    with formatted values and data types from the data dictionary.

    # Example usage:
    # Provide the path to your TSV data and HTM header files, along with the parsed data dictionary
    # data_tsv_path = 'path_to_your_data.tsv'
    # header_htm_path = 'path_to_your_header.htm'
    # Call the function with the required parameters
    # enhanced_unique_values = extract_unique_values_from_tsv(data_tsv_path, header_htm_path, data_dictionary)

    :param data_tsv_path: Path to the TSV data file.
    :param header_htm_path: Path to the HTM file containing data row definitions.
    :param data_dictionary_xls: Parsed data with format information and mappings.
    :param max_unique: Maximum number of unique values to store per column.
    :param name_of_sheet_with_dictionary: Name of the sheet that contains the data dictionary.
    :return: Dictionary with column names as keys and lists of unique values as values.
    """
    # Read and parse the XLS data dictionary file
    wb = openpyxl.load_workbook(data_dictionary_xls)
    sheet = wb[name_of_sheet_with_dictionary]
    data_dictionary = {}
    for row in tqdm(sheet.iter_rows(min_row=3, values_only=True),
                    desc='Parsing data dictionary', unit='rows'):
        var_name, data_field_value, data_field_formatted_value, data_type, *_ = row
        if var_name not in data_dictionary:
            data_dictionary[var_name] = {
                'valid_values': {},
                'data_type': data_type
            }
        data_dictionary[var_name]['valid_values'][
            data_field_value] = data_field_formatted_value

    # Parse HTM file to extract var_name using the first <td> of each <tr> in <tbody>
    with open(header_htm_path, 'r', encoding='windows-1252') as file:
        soup = BeautifulSoup(file, 'html.parser')
        var_name = [tr.find('td').text.strip() for tr in soup.select('tbody tr') if tr.find('td')]

    # Initialize a dictionary to collect unique observed values
    observed_values = {this_var: set() for this_var in var_name}

    # Read TSV file and extract unique values with a limit
    with open(data_tsv_path, 'r', encoding='utf-8') as file:
        reader = csv.DictReader(file, fieldnames=var_name, delimiter='\t')
        for row in tqdm(reader, desc='Extracting unique observed values', unit='rows'):
            for header in var_name:
                if len(observed_values[header]) < max_unique:
                    observed_values[header].add(row[header])

    # loop through data_dictionary and add observed values to data dictionary
    # alongside valid_values
    for var_name, var_data in data_dictionary.items():
        if var_name in observed_values:
            if 'observed_values' in var_data:
                raise ValueError(f"Duplicate observed values for {var_name}")
            else:  # Add observed values to the data dictionary
                var_data['observed_values'] = list(observed_values[var_name])

    # Return the data dictionary
    return data_dictionary

