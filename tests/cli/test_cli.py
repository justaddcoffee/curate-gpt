import os
import re
import warnings
from typing import List

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner
from tqdm import tqdm

from curate_gpt.cli import ontologize_unos_data, main, parse_html_for_columns, \
    process_row


@pytest.fixture(scope='session')
def runner():
    """
    Provides a CliRunner instance for testing CLI commands.
    """
    return CliRunner()


@pytest.fixture(scope='session')
def file_paths():
    """
    Prepares and provides file paths for testing, including paths for HTML, data, and mapping files.
    """
    base_dir = os.path.dirname(__file__)
    fixtures_dir = os.path.join(base_dir, '../../tests/fixtures/unos')

    # Define the paths to the actual fixtures
    html_file = os.path.join(fixtures_dir, 'THORACIC_DATA.htm')
    data_file = os.path.join(fixtures_dir, 'THORACIC_DATA_FAKE.DAT')
    mapping_file = os.path.join(fixtures_dir, 'UNOS_HPO_mappings.xlsx')

    # Dummy output file path
    # make temporary file for output
    import tempfile
    outfile_path = tempfile.mkstemp()[1]

    # Return paths in a dictionary
    return {
        'html_file': html_file,
        'data_file': data_file,
        'mapping_file': mapping_file,
        'outfile_path': outfile_path
    }


@pytest.fixture(scope='session')
def ontologize_unos_data_result(runner, file_paths):
    """
    Runs the ontologize_unos_data command once before all tests and stores the result.
    """
    result = runner.invoke(ontologize_unos_data, [
        file_paths['html_file'],  # HTML file
        file_paths['data_file'],  # Data file
        file_paths['mapping_file'],  # Mapping file
        file_paths['outfile_path'],  # Output file path
        '-l 1'
    ])
    assert result.exit_code == 0
    return result


@pytest.fixture(scope='session')
def vars_process_row(runner, file_paths):
    """
    Set up vars to run and test process_row()
    """

    columns, date_columns = parse_html_for_columns(file_paths['html_file'])
    hpo_mappings = pd.read_excel(file_paths['mapping_file'])

    col_names = [col[0] for col in columns]
    col_types = {col[0]: col[1] for col in columns if col[1] != 'datetime64'}

    # process_row(pt_row, hpo_mappings, exclude_forms, verbose):

    return {
        'columns': columns,
        'date_columns': date_columns,
        'hpo_mappings': hpo_mappings,
        'col_names': col_names,
        'col_types': col_types
    }


def test_help(runner):
    """
    Tests help message for the CLI application.
    """
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "index" in result.output


def test_ontologize_output_exists(ontologize_unos_data_result, file_paths):
    """
    Ensures that the output file was created after running ontologize_unos_data.
    """
    # Check that the output file was actually created
    assert os.path.exists(file_paths['outfile_path']), "Output file was not created"


def run_process_row(pos_value, variable_name, blank_row, vars_process_row, expected_hpo_term):
    brc = blank_row.copy()
    brc[variable_name] = pos_value
    # make sure row['HPO_term'] is in the hpo_terms
    assert expected_hpo_term in process_row(brc, vars_process_row['hpo_mappings'], [],True)


def test_hpo_term_outputs_are_correct(vars_process_row, file_paths):
    # check all mapping rows, one at a time, and ensure that we get the right HPO
    # term when we set the pt var to any of the positive values
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, '..', '..'))
    blank_row_tsv = os.path.join(project_root, 'tests', 'fixtures', 'unos', 'blank_row.tsv')

    blank_row = pd.read_csv(blank_row_tsv, sep='\t', header=None, index_col=0).squeeze()
    blank_row_hpo = process_row(blank_row, vars_process_row['hpo_mappings'], [], True)
    # assert there are no HPO terms in the blank_row_hpo
    assert len(blank_row_hpo) == 0

    # loop over values in
    for index, row in vars_process_row['hpo_mappings'].iterrows():
        if pd.notnull(row['HPO_term']):
            if row['data_type'] in ['C', 'CHAR(1)', 'CHAR(2)']:
                if '==' in row['function']:
                    for pos_value in [ors.split("==")[-1] for ors in row['function'].split("or")]:
                        run_process_row(pos_value, row['Variable_name'], blank_row, vars_process_row, row['HPO_term'])
                elif 'x in' in row['function']:
                    # Regular expression to match content inside brackets
                    pattern = re.compile(r"\[([^\]]+)\]")
                    matches = pattern.findall(row['function'])
                    # Loop through the matches and print each item
                    for match in matches:
                        # Remove spaces and split the string by commas
                        for pos_value in match.split(','):
                            run_process_row(pos_value, row['Variable_name'], blank_row,vars_process_row, row['HPO_term'])
            elif row['data_type'] in ['N', 'NUM']:
                if '==' in row['function']:
                    for pos_value in [ors.split("==")[-1] for ors in row['function'].split("or")]:
                        run_process_row(pos_value, row['Variable_name'], blank_row, vars_process_row, row['HPO_term'])
                elif 'x in' in row['function']:
                    # Regular expression to match content inside brackets
                    pattern = re.compile(r"\[([^\]]+)\]")
                    matches = pattern.findall(row['function'])
                    # Remove spaces and split the string by commas
                    for pos_value in matches[0].split(','):
                        run_process_row(pos_value, row['Variable_name'], blank_row, vars_process_row, row['HPO_term'])
                elif 'x <' in row['function'] or 'x >' in row['function']:
                    # match everything to the right of the > or <
                    for this_clause in row['function'].split("or"):
                        pattern = re.compile(r"(>|<)")
                        matches = pattern.split(this_clause)
                        if matches[1] == '>':
                            pos_value = float(matches[2]) + 1
                        elif matches[1] == '<':
                            pos_value = float(matches[2]) - 1
                        else:
                            raise ValueError(f"Weird match: {matches}")
                        run_process_row(pos_value, row['Variable_name'], blank_row, vars_process_row, row['HPO_term'])
                else:
                    warnings.warn(f"Deal with {row['function']}")
