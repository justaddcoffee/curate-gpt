import os
from typing import List

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner
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


def test_hpo_term_outputs_are_correct(vars_process_row, file_paths):
    blank_row_tsv = os.path.join(os.getcwd(), '../fixtures/unos/blank_row.tsv')
    blank_row = pd.read_csv(blank_row_tsv, sep='\t', header=None, index_col=0).squeeze()
    blank_row_hpo = process_row(blank_row, vars_process_row['hpo_mappings'], [], True)
    # assert there are no HPO terms in the blank_row_hpo
    assert len(blank_row_hpo) == 0


