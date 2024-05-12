import os
from curate_gpt.cli import main
from click.testing import CliRunner
from curate_gpt.cli import ontologize_unos_data


def test_help(runner):
    """
    Tests help message

    :param runner:
    :return:
    """
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "index" in result.output


def test_ontologize_unos_data():
    """
    Tests the basic execution of the ontologize_unos_data command using the specific HTML file.

    :return:
    """
    runner = CliRunner()
    fixtures_dir = os.path.join(os.path.dirname(__file__), '../../tests/fixtures/unos')

    # Define the paths to the actual fixtures
    html_file = os.path.join(fixtures_dir, 'THORACIC_DATA.htm')
    data_file = os.path.join(fixtures_dir, '../../../data/THORACIC_DATA.DAT')
    mapping_file = os.path.join(fixtures_dir, 'UNOS_HPO_mappings.xlsx')

    # Create dummy output file path
    outfile_path = 'output.txt'

    # Invoke the command with the specific file paths
    result = runner.invoke(ontologize_unos_data, [
        html_file,  # HTML file
        data_file,  # Data file
        mapping_file,  # Mapping file
        outfile_path,  # Output file path
        '-l 1'
    ])

    assert result.exit_code == 0
