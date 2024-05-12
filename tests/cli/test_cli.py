import os
import pytest
from click.testing import CliRunner
from curate_gpt.cli import ontologize_unos_data, main


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
    data_file = os.path.join(fixtures_dir, '../../../data/THORACIC_DATA.DAT')
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

