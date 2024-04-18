from bs4 import BeautifulSoup
import csv

from tqdm import tqdm


def extract_unique_values_from_tsv(data_tsv_path: str, header_htm_path: str, max_unique: int = 25) -> dict:
    """
    Extract unique values from a TSV file based on headers defined in an HTM file. This function specifically targets
    the first <td> element in each <tr> within <tbody> to use as headers, reflecting the provided HTML structure.

    :param data_tsv_path: Path to the TSV data file.
    :param header_htm_path: Path to the HTM file containing data row definitions.
    :param max_unique: Maximum number of unique values to store per column.
    :return: Dictionary with column names as keys and lists of unique values as values.
    """
    # Parse HTM file to extract headers using the first <td> of each <tr> in <tbody>
    with open(header_htm_path, 'r', encoding='windows-1252') as file:
        soup = BeautifulSoup(file, 'html.parser')
        headers = [tr.find('td').text.strip() for tr in soup.select('tbody tr') if tr.find('td')]

    # Initialize a dictionary to collect unique values
    unique_values = {header: set() for header in headers}

    # Read TSV file and extract unique values with a limit
    with open(data_tsv_path, 'r', encoding='utf-8') as file:
        reader = csv.DictReader(file, fieldnames=headers, delimiter='\t')
        for row in tqdm(reader, desc='Extracting unique values', unit='rows'):
            for header in headers:
                if len(unique_values[header]) < max_unique:
                    unique_values[header].add(row[header])

    # Convert sets to lists for JSON serialization
    return {header: list(values) for header, values in unique_values.items()}
