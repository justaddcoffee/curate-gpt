"""Command line interface for curate-gpt."""
import csv
import gzip
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Union

import click
import openai
import pandas as pd
import yaml
from click_default_group import DefaultGroup
from linkml_runtime.dumpers import json_dumper
from linkml_runtime.utils.yamlutils import YAMLRoot
from llm import UnknownModelError, get_model, get_plugins
from llm.cli import load_conversation
from oaklib import get_adapter
from pydantic import BaseModel
from bs4 import BeautifulSoup
from tqdm import tqdm

from curate_gpt import ChromaDBAdapter, __version__
from curate_gpt.agents.base_agent import BaseAgent
from curate_gpt.agents.chat_agent import ChatAgent, ChatResponse
from curate_gpt.agents.concept_recognition_agent import AnnotationMethod, ConceptRecognitionAgent
from curate_gpt.agents.dase_agent import DatabaseAugmentedStructuredExtraction
from curate_gpt.agents.dragon_agent import DragonAgent
from curate_gpt.agents.evidence_agent import EvidenceAgent
from curate_gpt.agents.summarization_agent import SummarizationAgent
from curate_gpt.evaluation.dae_evaluator import DatabaseAugmentedCompletionEvaluator
from curate_gpt.evaluation.evaluation_datamodel import StratifiedCollection, Task
from curate_gpt.evaluation.runner import run_task
from curate_gpt.evaluation.splitter import stratify_collection
from curate_gpt.extract import AnnotatedObject
from curate_gpt.extract.basic_extractor import BasicExtractor
from curate_gpt.store.schema_proxy import SchemaProxy
from curate_gpt.utils.parsing import _extract_unique_values_from_tsv
from curate_gpt.utils.vectordb_operations import match_collections
from curate_gpt.wrappers import BaseWrapper, get_wrapper
from curate_gpt.wrappers.literature.pubmed_wrapper import PubmedWrapper
from curate_gpt.wrappers.ontology import OntologyWrapper

__all__ = [
    "main",
]


def dump(obj: Union[str, AnnotatedObject, Dict], format="yaml") -> None:
    """
    Dump an object to stdout.

    :param obj:
    :param format:
    :return:
    """
    if isinstance(obj, str):
        print(obj)
        return
    if isinstance(obj, AnnotatedObject):
        obj = obj.object
    if isinstance(obj, BaseModel):
        obj = obj.dict()
    if isinstance(obj, YAMLRoot):
        obj = json_dumper.to_dict(obj)
    if format is None or format == "yaml":
        set = yaml.dump(obj, sort_keys=False)
    elif format == "json":
        set = json.dumps(obj, indent=2)
    elif format == "blob":
        set = list(obj.values())[0]
    else:
        raise ValueError(f"Unknown format {format}")
    print(set)


# logger = logging.getLogger(__name__)

path_option = click.option("-p", "--path", help="Path to a file or directory for database.")
model_option = click.option(
    "-m", "--model", help="Model to use for generation or embedding, e.g. gpt-4."
)
schema_option = click.option("-s", "--schema", help="Path to schema.")
collection_option = click.option("-c", "--collection", help="Collection within the database.")
output_format_option = click.option(
    "-t",
    "--output-format",
    type=click.Choice(["yaml", "json", "blob", "csv"]),
    default="yaml",
    show_default=True,
    help="Output format for results.",
)
relevance_factor_option = click.option(
    "--relevance-factor", type=click.FLOAT, help="Relevance factor for search."
)
generate_background_option = click.option(
    "--generate-background/--no-generate-background",
    default=False,
    show_default=True,
    help="Whether to generate background knowledge.",
)
limit_option = click.option(
    "-l", "--limit", default=10, show_default=True, help="Number of results to return."
)
replace_option = click.option(
    "--replace/--no-replace",
    default=False,
    show_default=True,
    help="replace the database before indexing.",
)
append_option = click.option(
    "--append/--no-append", default=False, show_default=True, help="Append to the database."
)
encoding_option = click.option(
    "--encoding",
    default="utf-8",
    show_default=True,
    help="Encoding for files, e.g. iso-8859-1, cp1252. Specify 'detect' to infer using chardet."
)
object_type_option = click.option(
    "--object-type",
    default="Thing",
    show_default=True,
    help="Type of object in index.",
)
description_option = click.option(
    "--description",
    help="Description of the collection.",
)
init_with_option = click.option(
    "--init-with",
    "-I",
    help="YAML string for initialization of main wrapper object.",
)
batch_size_option = click.option(
    "--batch-size", default=None, show_default=True, type=click.INT, help="Batch size for indexing."
)


def show_chat_response(response: ChatResponse, show_references: bool = True):
    """Show a chat response."""
    print("# Response:\n")
    click.echo(response.formatted_body)
    print("\n\n# Raw:\n")
    click.echo(response.body)
    if show_references:
        print("\n# References:\n")
        for ref, ref_text in response.references.items():
            print(f"\n## {ref}\n")
            print("```yaml")
            print(ref_text)
            print("```")
        print("# Uncited:")
        for ref, ref_text in response.uncited_references.items():
            print(f"\n## {ref}\n")
            print("```yaml")
            print(ref_text)
            print("```")


@click.group(
    cls=DefaultGroup,
    default="search",
    default_if_no_args=True,
)
@click.option("-v", "--verbose", count=True)
@click.option("-q", "--quiet")
@click.version_option(__version__)
def main(verbose: int, quiet: bool):
    """
    CLI for curate-gpt.

    :param verbose: Verbosity while running.
    :param quiet: Boolean to be quiet or verbose.
    """
    # logger = logging.getLogger()
    logging.basicConfig()
    logger = logging.root
    if verbose >= 2:
        logger.setLevel(level=logging.DEBUG)
    elif verbose == 1:
        logger.setLevel(level=logging.INFO)
    else:
        logger.setLevel(level=logging.WARNING)
    if quiet:
        logger.setLevel(level=logging.ERROR)
    logger.info(f"Logger {logger.name} set to level {logger.level}")


@main.command()
@path_option
@append_option
@collection_option
@model_option
@click.option("--text-field")
@object_type_option
@description_option
@click.option(
    "--view",
    "-V",
    help="View/Proxy to use for the database, e.g. bioc.",
)
@click.option(
    "--glob/--no-glob", default=False, show_default=True, help="Whether to glob the files."
)
@click.option(
    "--collect/--no-collect", default=False, show_default=True, help="Whether to collect files."
)
@click.option(
    "--select",
    help="jsonpath to use to subselect from each JSON document.",
)
@batch_size_option
@encoding_option
@click.argument("files", nargs=-1)
def index(
    files,
    path,
    append: bool,
    text_field,
    collection,
    model,
    object_type,
    description,
    batch_size,
    glob,
    view,
    select,
    collect,
    encoding,
    **kwargs,
):
    """
    Index files.

    Indexing a folder of JSON files:

        curategpt index  -c doc files/*json

    Here each file is treated as a separate object. It is loaded into the collection called 'doc'.

    Use --glob if there are too many files to expand on the command line:

        curategpt index --glob -c doc "files/*json"

    By default no transformation is performed on the objects. However, curategpt comes
    with standard views for common formats. For example, to index a folder of HPO associations

        curategpt index --view bacdive -c bacdive strains.json

    The --select option can be used to customize the path that will be used for indexing.
    For example:

         curategpt index -c cde_ncit --select '$.DataElementQueryResults' context-*.json

    This will index the DataElementQueryResults from each file.

    """
    db = ChromaDBAdapter(path, **kwargs)
    db.text_lookup = text_field
    if glob:
        files = [str(gf.absolute()) for f in files for gf in Path().glob(f) if gf.is_file()]
    if view:
        wrapper = get_wrapper(view)
        if not object_type:
            object_type = wrapper.default_object_type
        if not description:
            description = f"{object_type} objects loaded from {str(files)[0:30]}"
    else:
        wrapper = None
    if collect:
        raise NotImplementedError
    if not append:
        if collection in db.list_collection_names():
            db.remove_collection(collection)
    if model is None:
        model = "openai:"
    for file in files:
        if encoding == "detect":
            import chardet
            # Read the first num_lines of the file
            lines = []
            with open(file, 'rb') as f:
                try:
                    # Attempt to read up to num_lines lines from the file
                    for _ in range(100):
                        lines.append(next(f))
                except StopIteration:
                    # Reached the end of the file before reading num_lines lines
                    pass  # This is okay; just continue with the lines read so far
            # Concatenate lines into a single bytes object
            data = b''.join(lines)
            # Detect encoding
            result = chardet.detect(data)
            encoding = result['encoding']
        logging.debug(f"Indexing {file}")
        if wrapper:
            wrapper.source_locator = file
            objs = wrapper.objects()  # iterator
        elif file.endswith(".json"):
            objs = json.load(open(file))
        elif file.endswith(".csv"):
            with open(file, encoding=encoding) as f:
                objs = list(csv.DictReader(f))
        elif file.endswith(".tsv.gz"):
            with gzip.open(file, "rt", encoding=encoding) as f:
                objs = list(csv.DictReader(f, delimiter="\t"))
        elif file.endswith(".tsv"):
            objs = list(csv.DictReader(open(file, encoding=encoding), delimiter="\t"))
        else:
            objs = yaml.safe_load(open(file, encoding=encoding))
        if isinstance(objs, (dict, BaseModel)):
            objs = [objs]
        if select:
            import jsonpath_ng as jp
            path_expr = jp.parse(select)
            new_objs = []
            for obj in objs:
                for match in path_expr.find(obj):
                    logging.debug(f"Match: {match.value}")
                    if isinstance(match.value, list):
                        new_objs.extend(match.value)
                    else:
                        new_objs.append(match.value)
            objs = new_objs
        db.insert(objs, model=model, collection=collection, batch_size=batch_size)
    db.update_collection_metadata(
        collection, model=model, object_type=object_type, description=description
    )


@main.command(name="search")
@path_option
@collection_option
@limit_option
@relevance_factor_option
@click.option(
    "--show-documents/--no-show-documents",
    default=False,
    show_default=True,
    help="Whether to show documents/text (e.g. for chromadb).",
)
@click.argument("query")
def search(query, path, collection, show_documents, **kwargs):
    """Search a collection using embedding search."""
    db = ChromaDBAdapter(path)
    results = db.search(query, collection=collection, **kwargs)
    i = 0
    for obj, distance, meta in results:
        i += 1
        print(f"## {i} DISTANCE: {distance}")
        print(yaml.dump(obj, sort_keys=False))
        if show_documents:
            print("```")
            print(meta)
            print("```")


@main.command(name="all-by-all")
@path_option
@collection_option
@limit_option
@relevance_factor_option
@click.option(
    "--other-collection",
    "-X",
    help="Other collection to compare against.",
)
@click.option(
    "--other-path",
    "-P",
    help="Path for other collection (defaults to main path).",
)
@click.option(
    "--threshold",
    type=click.FLOAT,
    help="Cosine smilarity threshold for matches.",
)
@click.option(
    "--ids-only/--no-ids-only",
    default=False,
    show_default=True,
    help="Whether to show only ids.",
)
@click.option(
    "--left-field",
    "-L",
    multiple=True,
    help="Field to show from left collection (can provide multiple).",
)
@click.option(
    "--right-field",
    "-R",
    multiple=True,
    help="Field to show from right collection (can provide multiple).",
)
@output_format_option
def all_by_all(
    path,
    collection,
    other_collection,
    other_path,
    threshold,
    ids_only,
    output_format,
    left_field,
    right_field,
    **kwargs,
):
    """Match two collections."""
    db = ChromaDBAdapter(path)
    if other_path is None:
        other_path = path
    other_db = ChromaDBAdapter(other_path)
    results = match_collections(db, collection, other_collection, other_db)

    def _obj(obj: Dict, is_left=False) -> Any:
        if ids_only:
            obj = {"id": obj["id"]}
        if is_left and left_field:
            return {f"left_{k}": obj[k] for k in left_field}
        if not is_left and right_field:
            return {f"right_{k}": obj[k] for k in right_field}
        side = "left" if is_left else "right"
        obj = {f"{side}_{k}": v for k, v in obj.items()}
        return obj

    i = 0
    for obj1, obj2, sim in results:
        if threshold and sim < threshold:
            continue
        i += 1
        obj1 = _obj(obj1, is_left=True)
        obj2 = _obj(obj2, is_left=False)
        row = {**obj1, **obj2, "similarity": sim}
        if output_format == "csv":
            if i == 1:
                fieldnames = list(row.keys())
                dw = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
                dw.writeheader()
            dw.writerow({k: v for k, v in row.items() if k in fieldnames})
            continue

        print(f"\n## Match {i} COSINE SIMILARITY: {sim}")
        dump(obj1, output_format)
        dump(obj2, output_format)


@main.command()
@path_option
@collection_option
@click.argument("id")
def matches(id, path, collection):
    """Find matches for an ID."""
    db = ChromaDBAdapter(path)
    # TODO: persist this in the database
    db.text_lookup = "label"
    obj = db.lookup(id, collection=collection)
    print(obj)
    results = db.matches(obj, collection=collection)
    i = 0
    for obj, distance in results:
        print(f"## {i} DISTANCE: {distance}")
        print(yaml.dump(obj, sort_keys=False))


@main.command()
@path_option
@collection_option
@model_option
@limit_option
@click.option(
    "--identifier-field",
    "-I",
    help="Field to use as identifier (defaults to id).",
)
@click.option(
    "--label-field",
    "-L",
    help="Field to use as label (defaults to label).",
)
@click.option("-l", "--limit", default=50, show_default=True, help="Number of candidate terms.")
@click.option(
    "--input-file",
    "-i",
    type=click.File("r"),
    help="Input file (one text per line).",
)
@click.option(
    "--split-sentences/--no-split-sentences",
    "-s/-S",
    default=False,
    show_default=True,
    help="Whether to split sentences.",
)
# choose from options in AnnotationMethod
@click.option(
    "--method",
    "-M",
    default=AnnotationMethod.INLINE.value,
    show_default=True,
    type=click.Choice([m for m in AnnotationMethod]),
    help="Annotation method.",
)
@click.option(
    "--prefix",
    multiple=True,
    help="Prefix(es) for candidate IDs.",
)
@click.option(
    "--category",
    multiple=True,
    help="Category/ies for candidate IDs.",
)
@click.argument("texts", nargs=-1)
def annotate(
    texts,
    path,
    model,
    collection,
    input_file,
    split_sentences,
    category,
    prefix,
    identifier_field,
    label_field,
    **kwargs,
):
    """Concept recognition."""
    db = ChromaDBAdapter(path)
    extractor = BasicExtractor()
    if input_file:
        texts = [line.strip() for line in input_file]
    if model:
        extractor.model_name = model
    # TODO: persist this in the database
    cr = ConceptRecognitionAgent(knowledge_source=db, extractor=extractor)
    if prefix:
        cr.prefixes = list(prefix)
    categories = list(category) if category else None
    if identifier_field:
        cr.identifier_field = identifier_field
    if label_field:
        cr.label_field = label_field
    if split_sentences:
        new_texts = []
        for text in texts:
            for sentence in text.split("."):
                new_texts.append(sentence.strip())
        texts = new_texts
    for text in texts:
        ao = cr.annotate(text, collection=collection, categories=categories, **kwargs)
        dump(ao)
        print("---\n")


@main.command()
@path_option
@collection_option
@click.option(
    "-C/--no-C",
    "--conversation/--no-conversation",
    default=False,
    show_default=True,
    help="Whether to run in conversation mode.",
)
@model_option
@limit_option
@click.option(
    "--fields-to-predict",
    multiple=True,
)
@click.option(
    "--docstore-path",
    default=None,
    help="Path to a docstore to for additional unstructured knowledge.",
)
@click.option("--docstore-collection", default=None, help="Collection to use in the docstore.")
@generate_background_option
@click.option(
    "--rule",
    multiple=True,
    help="Rule to use for generating background knowledge.",
)
@schema_option
@click.option(
    "--input",
    "-i",
    default=None,
    help="Input file to extract.",
)
@output_format_option
@click.argument("text", nargs=-1)
def extract(
    text,
    input,
    path,
    docstore_path,
    docstore_collection,
    conversation,
    rule: List[str],
    model,
    schema,
    output_format,
    **kwargs,
):
    """Extract a structured object from text.

    This uses RAG to provide the most relevant example objects
    from the collection to guide the extraction.

    Example:

        curategpt extract -c ont_foodon \
           "Chip butties are scottish delicacies consisting of \
            a buttered roll filled with deep fried potato wedges"

    """
    db = ChromaDBAdapter(path)
    if schema:
        schema_manager = SchemaProxy(schema)
    else:
        schema_manager = None

    # TODO: generalize
    filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}
    extractor = BasicExtractor()
    if model:
        extractor.model_name = model
    if schema_manager:
        db.schema_proxy = schema
        extractor.schema_proxy = schema_manager
    agent = DatabaseAugmentedStructuredExtraction(knowledge_source=db, extractor=extractor)
    if docstore_path or docstore_collection:
        agent.document_adapter = ChromaDBAdapter(docstore_path)
        agent.document_adapter_collection = docstore_collection
    if not text:
        if not input:
            raise ValueError("Must provide either text or input file.")
        text = list(open(input).readlines())
    text = "\n".join(text)
    ao = agent.extract(text, rules=rule, **filtered_kwargs)
    dump(ao.object, format=output_format)


@main.command()
@path_option
@collection_option
@click.option(
    "-C/--no-C",
    "--conversation/--no-conversation",
    default=False,
    show_default=True,
    help="Whether to run in conversation mode.",
)
@model_option
@limit_option
@click.option(
    "--fields-to-predict",
    multiple=True,
)
@click.option(
    "--docstore-path",
    default=None,
    help="Path to a docstore to for additional unstructured knowledge.",
)
@click.option("--docstore-collection", default=None, help="Collection to use in the docstore.")
@generate_background_option
@click.option(
    "--rule",
    multiple=True,
    help="Rule to use for generating background knowledge.",
)
@click.option(
    "--output-directory",
    "-o",
    required=True,
)
@schema_option
@click.option(
    "--pubmed-id-file",
)
@click.argument("ids", nargs=-1)
def extract_from_pubmed(
    ids,
    pubmed_id_file,
    output_directory,
    path,
    docstore_path,
    docstore_collection,
    conversation,
    rule: List[str],
    model,
    schema,
    **kwargs,
):
    """Extract structured knowledge from a publication using its PubMed ID.

    See the `extract` command
    """
    db = ChromaDBAdapter(path)
    if schema:
        schema_manager = SchemaProxy(schema)
    else:
        schema_manager = None

    # TODO: generalize
    filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}
    extractor = BasicExtractor()
    if model:
        extractor.model_name = model
    if schema_manager:
        db.schema_proxy = schema
        extractor.schema_proxy = schema_manager
    agent = DatabaseAugmentedStructuredExtraction(knowledge_source=db, extractor=extractor)
    if docstore_path or docstore_collection:
        agent.document_adapter = ChromaDBAdapter(docstore_path)
        agent.document_adapter_collection = docstore_collection
    if not ids:
        if not pubmed_id_file:
            raise ValueError("Must provide either text or input file.")
        ids = [x.strip() for x in open(pubmed_id_file).readlines()]
    pmw = PubmedWrapper()
    output_directory = Path(output_directory)
    output_directory.mkdir(exist_ok=True, parents=True)
    for pmid in ids:
        pmid_esc = pmid.replace(":", "_")
        text = pmw.fetch_full_text(pmid)
        ao = agent.extract(text, rules=rule, **filtered_kwargs)
        with open(output_directory / f"{pmid_esc}.yaml", "w") as f:
            f.write(yaml.dump(ao.object, sort_keys=False))
        with open(output_directory / f"{pmid_esc}.txt", "w") as f:
            f.write(text)


@main.command()
@path_option
@collection_option
@click.option(
    "-C/--no-C",
    "--conversation/--no-conversation",
    default=False,
    show_default=True,
    help="Whether to run in conversation mode.",
)
@model_option
@limit_option
@click.option(
    "-P", "--query-property", default="label", show_default=True, help="Property to use for query."
)
@click.option(
    "--fields-to-predict",
    multiple=True,
)
@click.option(
    "--docstore-path",
    default=None,
    help="Path to a docstore to for additional unstructured knowledge.",
)
@click.option("--docstore-collection", default=None, help="Collection to use in the docstore.")
@generate_background_option
@click.option(
    "--rule",
    multiple=True,
    help="Rule to use for generating background knowledge.",
)
@schema_option
@output_format_option
@click.argument("query")
def complete(
    query,
    path,
    docstore_path,
    docstore_collection,
    conversation,
    rule: List[str],
    model,
    query_property,
    schema,
    output_format,
    **kwargs,
):
    """
    Generate an entry from a query using object completion.

    Example:
    -------

        curategpt complete  -c obo_go "umbelliferose biosynthetic process"

    If the string looks like yaml (if it has a ':') then it will be parsed as yaml.

    E.g

        curategpt complete  -c obo_go "label: umbelliferose biosynthetic process"
    """
    db = ChromaDBAdapter(path)
    if schema:
        schema_manager = SchemaProxy(schema)
    else:
        schema_manager = None

    # TODO: generalize
    filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}
    extractor = BasicExtractor()
    if model:
        extractor.model_name = model
    if schema_manager:
        db.schema_proxy = schema
        extractor.schema_proxy = schema_manager
    dac = DragonAgent(knowledge_source=db, extractor=extractor)
    if docstore_path or docstore_collection:
        dac.document_adapter = ChromaDBAdapter(docstore_path)
        dac.document_adapter_collection = docstore_collection
    if ":" in query:
        query = yaml.safe_load(query)
    ao = dac.complete(query, context_property=query_property, rules=rule, **filtered_kwargs)
    dump(ao.object, format=output_format)


@main.command()
@path_option
@collection_option
@click.option(
    "-C/--no-C",
    "--conversation/--no-conversation",
    default=False,
    show_default=True,
    help="Whether to run in conversation mode.",
)
@model_option
@limit_option
@click.option(
    "-P", "--query-property", default="label", show_default=True, help="Property to use for query."
)
@click.option(
    "--fields-to-predict",
    multiple=True,
)
@click.option(
    "--docstore-path",
    default=None,
    help="Path to a docstore to for additional unstructured knowledge.",
)
@click.option("--docstore-collection", default=None, help="Collection to use in the docstore.")
@generate_background_option
@click.option(
    "--rule",
    multiple=True,
    help="Rule to use for generating background knowledge.",
)
@schema_option
@output_format_option
@click.argument("input_file")
def complete_multiple(
    input_file,
    path,
    docstore_path,
    docstore_collection,
    conversation,
    rule: List[str],
    model,
    query_property,
    schema,
    output_format,
    **kwargs,
):
    """
    Generate an entry from a query using object completion for multiple objects.

    Example:
    -------
        curategpt generate  -c obo_go terms.txt
    """
    db = ChromaDBAdapter(path)
    if schema:
        schema_manager = SchemaProxy(schema)
    else:
        schema_manager = None

    # TODO: generalize
    filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}
    extractor = BasicExtractor()
    if model:
        extractor.model_name = model
    if schema_manager:
        db.schema_proxy = schema
        extractor.schema_proxy = schema_manager
    dac = DragonAgent(knowledge_source=db, extractor=extractor)
    if docstore_path or docstore_collection:
        dac.document_adapter = ChromaDBAdapter(docstore_path)
        dac.document_adapter_collection = docstore_collection
    with open(input_file) as f:
        queries = [l.strip() for l in f.readlines()]
        for query in queries:
            if ":" in query:
                query = yaml.safe_load(query)
            ao = dac.complete(query, context_property=query_property, rules=rule, **filtered_kwargs)
            print("---")
            dump(ao.object, format=output_format)


@main.command()
@path_option
@collection_option
@click.option(
    "-C/--no-C",
    "--conversation/--no-conversation",
    default=False,
    show_default=True,
    help="Whether to run in conversation mode.",
)
@model_option
@limit_option
@click.option("--field-to-predict", "-F", help="Field to predict")
@click.option(
    "--docstore-path",
    default=None,
    help="Path to a docstore to for additional unstructured knowledge.",
)
@click.option("--docstore-collection", default=None, help="Collection to use in the docstore.")
@click.option(
    "--generate-background/--no-generate-background",
    default=False,
    show_default=True,
    help="Whether to generate background knowledge.",
)
@click.option(
    "--rule",
    multiple=True,
    help="Rule to use for generating background knowledge.",
)
@click.option(
    "--id-file",
    "-i",
    type=click.File("r"),
    help="File to read ids from.",
)
@click.option(
    "--missing-only/--no-missing-only",
    default=True,
    show_default=True,
    help="Only generate missing values.",
)
@schema_option
def complete_all(
    path,
    collection,
    docstore_path,
    docstore_collection,
    conversation,
    rule: List[str],
    model,
    field_to_predict,
    schema,
    id_file,
    **kwargs,
):
    """
    Generate missing values for all objects

    Example:
    -------
        curategpt generate  -c obo_go TODO
    """
    db = ChromaDBAdapter(path)
    if schema:
        schema_manager = SchemaProxy(schema)
    else:
        schema_manager = None

    # TODO: generalize
    filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}
    extractor = BasicExtractor()
    if model:
        extractor.model_name = model
    if schema_manager:
        db.schema_proxy = schema
        extractor.schema_proxy = schema_manager
    dae = DragonAgent(knowledge_source=db, extractor=extractor)
    if docstore_path or docstore_collection:
        dae.document_adapter = ChromaDBAdapter(docstore_path)
        dae.document_adapter_collection = docstore_collection
    object_ids = None
    if id_file:
        object_ids = [line.strip() for line in id_file.readlines()]
    it = dae.generate_all(
        collection=collection,
        field_to_predict=field_to_predict,
        rules=rule,
        object_ids=object_ids,
        **filtered_kwargs,
    )
    for pred in it:
        print(yaml.dump(pred.dict(), sort_keys=False))


@main.command()
@path_option
@collection_option
@click.option("--test-collection", "-T", required=True, help="Collection to use as the test set")
@click.option(
    "--hold-back-fields",
    "-F",
    required=True,
    help="Comma separated list of fields to predict in the test.",
)
@click.option(
    "--mask-fields",
    "-M",
    help="Comma separated list of fields to mask in the test.",
)
@model_option
@limit_option
@click.option(
    "--docstore-path",
    default=None,
    help="Path to a docstore to for additional unstructured knowledge.",
)
@click.option("--docstore-collection", default=None, help="Collection to use in the docstore.")
@click.option(
    "--generate-background/--no-generate-background",
    default=False,
    show_default=True,
    help="Whether to generate background knowledge.",
)
@click.option(
    "--rule",
    multiple=True,
    help="Rule to use for generating background knowledge.",
)
@click.option(
    "--report-file",
    "-o",
    type=click.File("w"),
    help="File to write report to.",
)
@click.option(
    "--num-tests",
    default=None,
    show_default=True,
    help="Number (max) of tests to run.",
)
@schema_option
def generate_evaluate(
    path,
    docstore_path,
    docstore_collection,
    model,
    schema,
    test_collection,
    num_tests,
    hold_back_fields,
    mask_fields,
    rule: List[str],
    **kwargs,
):
    """
    Evaluate generate using a test set.

    Example:
    -------
        curategpt -v generate-evaluate -c cdr_training -T cdr_test -F statements -m gpt-4
    """
    db = ChromaDBAdapter(path)
    if schema:
        schema_manager = SchemaProxy(schema)
    else:
        schema_manager = None

    # filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}
    extractor = BasicExtractor()
    if model:
        extractor.model_name = model
    if schema_manager:
        db.schema_proxy = schema
        extractor.schema_proxy = schema_manager
    rage = DragonAgent(knowledge_source=db, extractor=extractor)
    if docstore_path or docstore_collection:
        rage.document_adapter = ChromaDBAdapter(docstore_path)
        rage.document_adapter_collection = docstore_collection
    hold_back_fields = hold_back_fields.split(",")
    mask_fields = mask_fields.split(",") if mask_fields else []
    evaluator = DatabaseAugmentedCompletionEvaluator(
        agent=rage, fields_to_predict=hold_back_fields, fields_to_mask=mask_fields
    )
    results = evaluator.evaluate(test_collection, num_tests=num_tests, **kwargs)
    print(yaml.dump(results.dict(), sort_keys=False))


@main.command()
@path_option
@collection_option
@click.option(
    "--hold-back-fields",
    "-F",
    help="Comma separated list of fields to predict in the test.",
)
@click.option(
    "--mask-fields",
    "-M",
    help="Comma separated list of fields to mask in the test.",
)
@model_option
@limit_option
@click.option(
    "--generate-background/--no-generate-background",
    default=False,
    show_default=True,
    help="Whether to generate background knowledge.",
)
@click.option(
    "--rule",
    multiple=True,
    help="Rule to use for generating background knowledge.",
)
@click.option(
    "--report-file",
    "-o",
    type=click.File("w"),
    help="File to write report to.",
)
@click.option(
    "--num-testing",
    default=None,
    show_default=True,
    help="Number (max) of tests to run.",
)
@click.option(
    "--working-directory",
    "-W",
    help="Working directory to use.",
)
@click.option(
    "--fresh/--no-fresh",
    default=False,
    show_default=True,
    help="Whether to rebuild test/train collections.",
)
@generate_background_option
@click.argument("tasks", nargs=-1)
def evaluate(
    tasks,
    working_directory,
    path,
    model,
    generate_background,
    num_testing,
    hold_back_fields,
    mask_fields,
    rule: List[str],
    collection,
    **kwargs,
):
    """
    Evaluate given a task configuration.

    Example:
    -------
        curategpt evaluate src/curate_gpt/conf/tasks/bio-ont.tasks.yaml
    """
    normalized_tasks = []
    for task in tasks:
        if ":" in task:
            task = yaml.safe_load(task)
        else:
            task = yaml.safe_load(open(task))
        if isinstance(task, list):
            normalized_tasks.extend(task)
        else:
            normalized_tasks.append(task)
    for task in normalized_tasks:
        task_obj = Task(**task)
        if path:
            task_obj.path = path
        if working_directory:
            task_obj.working_directory = working_directory
        if collection:
            task_obj.source_collection = collection
        if model:
            task_obj.model_name = model
        if hold_back_fields:
            task_obj.hold_back_fields = hold_back_fields.split(",")
        if mask_fields:
            task_obj.mask_fields = mask_fields.split(",")
        if num_testing is not None:
            task_obj.num_testing = int(num_testing)
        if generate_background:
            task_obj.generate_background = generate_background
        if rule:
            # TODO
            task_obj.rules = rule
        result = run_task(task_obj, **kwargs)
        print(yaml.dump(result.dict(), sort_keys=False))


@main.command()
@click.option("--collections", required=True)
@click.option("--models", default="gpt-3.5-turbo")
@click.option("--fields-to-mask", default="id,original_id")
@click.option("--fields-to-predict", required=True)
@click.option("--num-testing", default=50, show_default=True)
@click.option("--background", default="false", show_default=True)
def evaluation_config(collections, models, fields_to_mask, fields_to_predict, background, **kwargs):
    tasks = []
    for collection in collections.split(","):
        for model in models.split(","):
            for fp in fields_to_predict.split(","):
                for bg in background.split(","):
                    tc = Task(
                        source_db_path="db",
                        target_db_path="db",
                        model_name=model,
                        source_collection=collection,
                        fields_to_predict=[fp],
                        fields_to_mask=fields_to_mask.split(","),
                        generate_background=json.loads(bg),
                        stratified_collection=StratifiedCollection(
                            training_set_collection=f"{collection}_training",
                            testing_set_collection=f"{collection}_testing",
                        ),
                        **kwargs,
                    )
                    tasks.append(tc.dict(exclude_unset=True))
    print(yaml.dump(tasks, sort_keys=False))


@main.command()
@click.option(
    "--include-expected/--no-include-expected",
    "-E",
    default=False,
    show_default=True,
)
@click.argument("files", nargs=-1)
def evaluation_compare(files, include_expected=False):
    """Compare evaluation results."""
    dfs = []
    predicted_cols = []
    other_cols = []
    differentia_col = "method"
    for f in files:
        df = pd.read_csv(f, sep="\t", comment="#")
        df[differentia_col] = f
        if include_expected:
            include_expected = False
            base_df = df.copy()
            base_df[differentia_col] = "source"
            for c in base_df.columns:
                if c.startswith("expected_"):
                    new_col = c.replace("expected_", "predicted_")
                    base_df[new_col] = base_df[c]
            dfs.append(base_df)
        dfs.append(df)
        for c in df.columns:
            if c in predicted_cols or c in other_cols:
                continue
            if c.startswith("predicted_"):
                predicted_cols.append(c)
            else:
                other_cols.append(c)
    df = pd.concat(dfs)
    # df = pd.melt(df, id_vars=["masked_id", "file"], value_vars=["predicted_definition"])
    df = pd.melt(df, id_vars=list(other_cols), value_vars=list(predicted_cols))
    df = df.sort_values(by=list(other_cols))
    df.to_csv(sys.stdout, sep="\t", index=False)


@main.command()
@click.option(
    "--system",
    "-s",
    help="System gpt prompt to use.",
)
@click.option(
    "--prompt",
    "-p",
    default="What is the definition of {column}?",
)
@model_option
@click.argument("file")
def multiprompt(file, model, system, prompt):
    if model is None:
        model = "gpt-3.5-turbo"
    model_obj = get_model(model)
    with open(file) as f:
        for row in csv.DictReader(f, delimiter="\t"):
            resp = model_obj.prompt(system=system, prompt=prompt.format(**row)).text()
            resp = resp.replace("\n", " ")
            print("\t".join(list(row.values()) + [resp]))


@main.command()
@collection_option
@path_option
@model_option
@click.option(
    "--show-references/--no-show-references",
    default=True,
    show_default=True,
    help="Whether to show references.",
)
@click.option(
    "_continue",
    "-C",
    "--continue",
    is_flag=True,
    flag_value=-1,
    help="Continue the most recent conversation.",
)
@click.option(
    "conversation_id",
    "--cid",
    "--conversation",
    help="Continue the conversation with the given ID.",
)
@click.argument("query")
def ask(query, path, collection, model, show_references, _continue, conversation_id):
    """Chat with data in a collection.

    Example:

        curategpt ask -c obo_go "What are the parts of the nucleus?"
    """
    db = ChromaDBAdapter(path)
    extractor = BasicExtractor()
    if model:
        extractor.model_name = model
    conversation = None
    if conversation_id or _continue:
        # Load the conversation - loads most recent if no ID provided
        try:
            conversation = load_conversation(conversation_id)
            print(f"CONTINUING CONVERSATION {conversation}")
        except UnknownModelError as ex:
            raise click.ClickException(str(ex)) from ex
    chatbot = ChatAgent(path)
    chatbot.extractor = extractor
    chatbot.knowledge_source = db
    response = chatbot.chat(query, collection=collection, conversation=conversation)
    show_chat_response(response, show_references)


@main.command()
@collection_option
@path_option
@model_option
@click.option(
    "--show-references/--no-show-references",
    default=True,
    show_default=True,
    help="Whether to show references.",
)
@click.option(
    "_continue",
    "-C",
    "--continue",
    is_flag=True,
    flag_value=-1,
    help="Continue the most recent conversation.",
)
@click.option(
    "conversation_id",
    "--cid",
    "--conversation",
    help="Continue the conversation with the given ID.",
)
@click.argument("query")
def citeseek(query, path, collection, model, show_references, _continue, conversation_id):
    """Find citations for an object."""
    db = ChromaDBAdapter(path)
    extractor = BasicExtractor()
    if model:
        extractor.model_name = model
    chatbot = ChatAgent(db, extractor=extractor, knowledge_source_collection=collection)
    ea = EvidenceAgent(chat_agent=chatbot)
    response = ea.find_evidence(query)
    print("# Response:")
    click.echo(response.formatted_body)
    print("# Raw:")
    click.echo(response.body)
    if show_references:
        print("# References:")
        for ref, ref_text in response.references.items():
            print(f"## {ref}")
            print(ref_text)


@main.command()
@collection_option
@path_option
@model_option
@click.option("--view", "-V", help="Name of the wrapper to use.")
@click.option("--name-field", help="Field for names.")
@click.option("--description-field", help="Field for descriptions.")
@click.option("--system-prompt", help="System gpt prompt to use.")
@click.argument("ids", nargs=-1)
def summarize(ids, path, collection, model, view, **kwargs):
    """
    Summarize a list of objects.

    Retrieves objects by ID from a knowledge source or wrapper and summarizes them.

    (this is a partial implementation of TALISMAN using CurateGPT)

    Example:
    -------
      curategpt summarize --model llama-2-7b-chat -V alliance_gene \
        --name-field symbol --description-field automatedGeneSynopsis \
        --system-prompt "What functions do these genes share?" \
        HGNC:3239 HGNC:7632 HGNC:4458 HGNC:9439 HGNC:29427 \
        HGNC:1160  HGNC:26270 HGNC:24682 HGNC:7225 HGNC:13797 \
        HGNC:9118  HGNC:6396  HGNC:9179 HGNC:25358
    """
    db = ChromaDBAdapter(path)
    extractor = BasicExtractor()
    if model:
        extractor.model_name = model
    if view:
        db = get_wrapper(view)
    agent = SummarizationAgent(db, extractor=extractor, knowledge_source_collection=collection)
    response = agent.summarize(ids, **kwargs)
    print("# Response:")
    click.echo(response)


@main.command()
def plugins():
    "List installed plugins"
    print(yaml.dump(get_plugins()))


@main.group()
def collections():
    "Operate on collections in the store."


@collections.command(name="list")
@click.option(
    "--minimal/--no-minimal",
    default=False,
    show_default=True,
    help="Whether to show minimal information.",
)
@click.option(
    "--derived/--no-derived",
    default=True,
    show_default=True,
    help="Whether to show derived information.",
)
@click.option(
    "--peek/--no-peek",
    default=False,
    show_default=True,
    help="Whether to peek at the first few entries of the collection.",
)
@path_option
def list_collections(path, peek: bool, minimal: bool, derived: bool):
    """List all collections."""
    db = ChromaDBAdapter(path)
    for cn in db.collections():
        if minimal:
            print(f"## Collection: {cn}")
            continue
        cm = db.collection_metadata(cn, include_derived=derived)
        c = db.client.get_or_create_collection(cn)
        print(f"## Collection: {cn} N={c.count()} meta={c.metadata} // {cm}")
        if peek:
            r = c.peek()
            for id in r["ids"]:
                print(f" - {id}")


@collections.command(name="delete")
@collection_option
@path_option
def delete_collection(path, collection):
    """Delete a collections."""
    db = ChromaDBAdapter(path)
    db.remove_collection(collection)


@collections.command(name="peek")
@collection_option
@limit_option
@path_option
def peek_collection(path, collection, **kwargs):
    """Inspect a collection."""
    logging.info(f"Peeking at {collection} in {path}")
    db = ChromaDBAdapter(path)
    for obj in db.peek(collection, **kwargs):
        print(yaml.dump(obj, sort_keys=False))


@collections.command(name="dump")
@collection_option
@click.option("-o", "--output", type=click.File("w"), default="-")
@click.option("--metadata-to-file", type=click.File("w"), default=None)
@click.option("--format", "-t", default="json", show_default=True)
@click.option("--include", "-I", multiple=True, help="Include a field.")
@path_option
def dump_collection(path, collection, output, **kwargs):
    """
    Dump a collection to disk.

    There are two flavors of format:

    - streaming, flat lists of objects (e.g. jsonl)
    - complete (e.g json)

    with streaming formats it's necessary to also provide `--metadata-to-file` since
    the metadata header won't fit into the line-based formats.

    Example:

        curategpt collections dump  -c ont_cl -o cl.cur.json

    Example:

        curategpt collections dump  -c ont_cl -o cl.cur.jsonl -t jsonl --metadata-to-file cl.meta.json

    TODO: venomx support
    """
    logging.info(f"Dumping {collection} in {path}")
    db = ChromaDBAdapter(path)
    db.dump(collection, to_file=output, **kwargs)


@collections.command(name="copy")
@collection_option
@click.option("--target-path")
@path_option
def copy_collection(path, collection, target_path, **kwargs):
    """
    Copy a collection from one path to another.

    Example:

        curategpt collections copy -p stagedb --target-path db -c my_collection
    """
    logging.info(f"Copying {collection} in {path} to {target_path}")
    db = ChromaDBAdapter(path)
    target = ChromaDBAdapter(target_path)
    db.dump_then_load(collection, target=target, **kwargs)


@collections.command(name="split")
@collection_option
@click.option(
    "--derived-collection-base",
    help=(
        "Base name for derived collections. Will be suffixed with _train, _test, _val."
        "If not provided, will use the same name as the original collection."
    ),
)
@model_option
@click.option(
    "--num-training",
    type=click.INT,
    help="Number of training examples to keep.",
)
@click.option(
    "--num-testing",
    type=click.INT,
    help="Number of testing examples to keep.",
)
@click.option(
    "--num-validation",
    default=0,
    show_default=True,
    type=click.INT,
    help="Number of validation examples to keep.",
)
@click.option(
    "--test-id-file",
    type=click.File("r"),
    help="File containing IDs of test objects.",
)
@click.option(
    "--ratio",
    type=click.FLOAT,
    help="Ratio of training to testing examples.",
)
@click.option(
    "--fields-to-predict",
    "-F",
    required=True,
    help="Comma separated list of fields to predict in the test. Candidate objects must have these fields.",
)
@click.option(
    "--output-path",
    "-o",
    required=True,
    help="Path to write the new store.",
)
@path_option
def split_collection(
    path, collection, derived_collection_base, output_path, model, test_id_file, **kwargs
):
    """
    Split a collection into test/train/validation.

    Example:
    -------
        curategpt -v collections split -c hp --num-training 10 --num-testing 20

    The above populates 2 new collections: hp_training and hp_testing.

    This can be run as a pre-processing step for generate-evaluate.
    """
    db = ChromaDBAdapter(path)
    if test_id_file:
        kwargs["testing_identifiers"] = [line.strip().split()[0] for line in test_id_file]
        logging.info(
            f"Using {len(kwargs['testing_identifiers'])} testing identifiers from {test_id_file.name}"
        )
        logging.info(f"First 10: {kwargs['testing_identifiers'][:10]}")
    sc = stratify_collection(db, collection, **kwargs)
    output_db = ChromaDBAdapter(output_path)
    if not derived_collection_base:
        derived_collection_base = collection
    for sn in ["training", "testing", "validation"]:
        cn = f"{derived_collection_base}_{sn}"
        output_db.remove_collection(cn, exists_ok=True)
        objs = getattr(sc, f"{sn}_set", [])
        logging.info(f"Writing {len(objs)} objects to {cn}")
        output_db.insert(objs, collection=cn, model=model)


@collections.command(name="set")
@collection_option
@path_option
@click.argument("metadata_yaml")
def set_collection_metadata(path, collection, metadata_yaml):
    """Set metadata for a collection."""
    db = ChromaDBAdapter(path)
    db.update_collection_metadata(collection, **yaml.safe_load(metadata_yaml))


@main.group()
def ontology():
    "Use the ontology model"


@ontology.command(name="index")
@path_option
@collection_option
@model_option
@append_option
@click.option(
    "--branches",
    "-b",
    help="Comma separated list node IDs representing branches to index.",
)
@click.option(
    "--index-fields",
    help="Fields to index; comma sepatrated",
)
@click.argument("ont")
def index_ontology_command(ont, path, collection, append, model, index_fields, branches, **kwargs):
    """
    Index an ontology.

    Example:
    -------
        curategpt index-ontology  -c obo_hp $db/hp.db

    """
    oak_adapter = get_adapter(ont)
    view = OntologyWrapper(oak_adapter=oak_adapter)
    if branches:
        view.branches = branches.split(",")
    db = ChromaDBAdapter(path, **kwargs)
    db.text_lookup = view.text_field
    if index_fields:
        fields = index_fields.split(",")

        # print(f"Indexing fields: {fields}")
        def _text_lookup(obj: Dict):
            vals = [str(obj.get(f)) for f in fields if f in obj]
            return " ".join(vals)

        db.text_lookup = _text_lookup
    if not append:
        db.remove_collection(collection, exists_ok=True)
    db.insert(view.objects(), collection=collection, model=model)
    db.update_collection_metadata(collection, object_type="OntologyClass")


@main.group()
def view():
    "Virtual store/wrapper"


@view.command(name="objects")
@click.option("--view", "-V", required=True, help="Name of the wrapper to use.")
@click.option("--source-locator")
@init_with_option
def view_objects(view, init_with, **kwargs):
    """
    View objects in a virtual store.

    Example:
    -------
        curategpt view objects -V filesystem --init-with "root_directory: /path/to/data"

    """
    if init_with:
        for k, v in yaml.safe_load(init_with).items():
            kwargs[k] = v
    vstore = get_wrapper(view, **kwargs)
    for obj in vstore.objects():
        print(yaml.dump(obj, sort_keys=False))


@view.command(name="unwrap")
@click.option("--view", "-V", required=True, help="Name of the wrapper to use.")
@click.option("--source-locator")
@path_option
@collection_option
@output_format_option
@click.argument("input_file")
def unwrap_objects(input_file, view, path, collection, output_format, **kwargs):
    """
    Unwrap objects back to source schema.

    Example:
    -------

    Todo:
    ----

    """
    vstore = get_wrapper(view, **kwargs)
    store = ChromaDBAdapter(path)
    store.set_collection(collection)
    with open(input_file) as f:
        objs = yaml.safe_load_all(f)
        unwrapped = vstore.unwrap_objects(objs, store=store)
        dump(unwrapped, output_format)


@view.command(name="search")
@click.option("--view", "-V")
@click.option("--source-locator")
@model_option
@limit_option
@init_with_option
@click.argument("query")
def view_search(query, view, model, init_with, limit, **kwargs):
    """Search in a virtual store."""
    if init_with:
        for k, v in yaml.safe_load(init_with).items():
            kwargs[k] = v
    vstore: BaseWrapper = get_wrapper(view, **kwargs)
    vstore.extractor = BasicExtractor(model_name=model)
    for obj, _dist, _ in vstore.search(query, limit=limit):
        print(yaml.dump(obj, sort_keys=False))


@view.command(name="index")
@path_option
@collection_option
@click.option("--view", "-V")
@click.option("--source-locator")
@batch_size_option
@model_option
@init_with_option
@append_option
def view_index(view, path, append, collection, model, init_with, batch_size, **kwargs):
    """Populate an index from a view."""
    if init_with:
        for k, v in yaml.safe_load(init_with).items():
            kwargs[k] = v
    wrapper: BaseWrapper = get_wrapper(view, **kwargs)
    store = ChromaDBAdapter(path)
    if not append:
        if collection in store.list_collection_names():
            store.remove_collection(collection)
    objs = wrapper.objects()
    store.insert(objs, model=model, collection=collection, batch_size=batch_size)


@view.command(name="ask")
@click.option("--view", "-V")
@click.option("--source-locator")
@limit_option
@model_option
@click.argument("query")
def view_ask(query, view, model, limit, **kwargs):
    """Ask a knowledge source wrapper."""
    vstore: BaseWrapper = get_wrapper(view)
    vstore.extractor = BasicExtractor(model_name=model)
    chatbot = ChatAgent(knowledge_source=vstore)
    response = chatbot.chat(query, limit=limit)
    show_chat_response(response, True)


@main.group()
def pubmed():
    "Use pubmed"


@pubmed.command(name="search")
@collection_option
@path_option
@model_option
@click.option(
    "--expand/--no-expand",
    default=True,
    show_default=True,
    help="Whether to expand the search term using an LLM.",
)
@click.argument("query")
def pubmed_search(query, path, model, **kwargs):
    pubmed = PubmedWrapper()
    db = ChromaDBAdapter(path)
    extractor = BasicExtractor()
    if model:
        extractor.model_name = model
    pubmed.extractor = extractor
    pubmed.local_store = db
    results = pubmed.search(query, **kwargs)
    i = 0
    for obj, distance, _ in results:
        i += 1
        print(f"## {i} DISTANCE: {distance}")
        print(yaml.dump(obj, sort_keys=False))


@pubmed.command(name="ask")
@collection_option
@path_option
@model_option
@limit_option
@click.option(
    "--show-references/--no-show-references",
    default=True,
    show_default=True,
    help="Whether to show references.",
)
@click.option(
    "--expand/--no-expand",
    default=True,
    show_default=True,
    help="Whether to expand the search term using an LLM.",
)
@click.argument("query")
def pubmed_ask(query, path, model, show_references, **kwargs):
    pubmed = PubmedWrapper()
    db = ChromaDBAdapter(path)
    extractor = BasicExtractor()
    if model:
        extractor.model_name = model
    pubmed.extractor = extractor
    pubmed.local_store = db
    response = pubmed.chat(query, **kwargs)
    click.echo(response.formatted_body)
    if show_references:
        print("# References:")
        for ref, ref_text in response.references.items():
            print(f"## {ref}")
            print(ref_text)


@click.command(name='extract-unique')
@click.option('--data-tsv', '-d', type=str, required=True,
              help="Path to the TSV data file.")
@click.option('--header-htm', '-h', type=str, required=True,
              help="Path to the HTM header file.")
@click.option('--data-dict-xls', '-x', type=str, required=True,
              help="Path to the XLS data dictionary file.")
@click.option('--output-dir', '-o', type=str, default='data',
              help="Directory to save the output file. Defaults to 'data/'.")
@click.option('--max-unique', '-m', type=int, default=25,
              help="Maximum number of unique values per column to retain.")
def extract_unique_values(data_tsv, header_htm, data_dict_xls, output_dir, max_unique):
    """
    Extract and display unique values from a specified TSV file based on headers defined in an HTM file
    and a XLS data dictionary file. This is a pretty specific util function, so if you're not sure what it does,
    you probably don't need it. Writes the unique values to a JSON file in the specified output directory, defaulting to 'data/'.
    """

    # Extract valid values and observed values for items in data dictionary
    parsed_data_dict = _extract_unique_values_from_tsv(DAT_tsv_file=data_tsv,
                                                       DAT_header_htm_file=header_htm,
                                                       data_dictionary_xls=data_dict_xls,
                                                       data_dictionary_sheet_name_for_dat_file="THORACIC_DATA", # noqa
                                                       max_unique=max_unique)

    # Ensure the output directory exists
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Construct the output file path
    data_file_name = Path(data_tsv).stem
    output_file_path = os.path.join(output_dir, f"{data_file_name}_parsed_data_dict.json")

    # Write the unique values to the output file in JSON format
    # with open(output_file_path, 'w', encoding='utf-8') as file:
    #     json.dump(parsed_data_dict, file, indent=4)

    # Writing the dictionary to a JSON file
    with open(output_file_path, 'w') as file:
        json.dump(parsed_data_dict, file, indent=4)  # `indent=4` for pretty printing

    print(f"Unique values have been written to {output_file_path}")


main.add_command(extract_unique_values)


@main.command()
@model_option
@path_option
@collection_option
@click.option('--parsed_data_dict', '-d', help='Path to the input JSON file with clinical variables.')
@click.option('--output_file', '-o', help='Path to the output file to save mappings.')
@click.option(
    "--prefix",
    multiple=True,
    help="Prefix(es) for candidate IDs.",
)
@click.option(
    "--identifier-field",
    "-I",
    help="Field to use as identifier (defaults to id).",
)
@click.option(
    "--label-field",
    "-L",
    help="Field to use as label (defaults to label).",
)
def make_unos_mapping_logic(path, model, collection, prefix,
                            identifier_field, label_field, parsed_data_dict,
                            output_file, **kwargs):

    # Load the data dictionary from JSON file
    with open(parsed_data_dict, 'r') as file:
        data_dict = json.load(file)

    # Prepare the YAML content dictionary
    yaml_content = []

    if model is None:
        model = "gpt-4-turbo"
    model_obj = get_model(model)

    # set up RAG
    """Concept recognition."""
    # db = ChromaDBAdapter(path)
    # extractor = BasicExtractor()
    # if model:
    #     extractor.model_name = model
    #
    # cr = ConceptRecognitionAgent(knowledge_source=db, extractor=extractor)
    # if prefix:
    #     cr.prefixes = list(prefix)
    # categories = list(category) if category else None
    # if identifier_field:
    #     cr.identifier_field = identifier_field
    # if label_field:
    #     cr.label_field = label_field

    db = ChromaDBAdapter(path, collection=collection)

    system = '''
You are tasked with mapping clinical variables to Human Phenotype Ontology (HPO) terms 
based on the descriptions of the variable and the observed and valid values. Below is 
the information that I will provide about the linical variable:

    "{variable_name}": {
        "description": {description},
        "form": {form from which data was collected},
        "var_start_date": {start date}
        "var_end_date": {end date}
        "form_section": {form section},
        "data_type": {type of data, numeric or categorical},
        "sas_analysis_format": {name of variable in SAS analysis format},
        "comment": "Collection ended 1/1/07 for Lung (see INIT_CREAT & END_CREAT instead) ",
        "valid_values": {list of valid values},
        "observed_values": {list of observed values}
    },
    
Use this information to determine appropriate HPO terms and construct YAML mappings.    

For example, here is a sample clinical variable from the data dictionary:
    "MOST_RCNT_CREAT": {
        "description": "PATIENT MOST RECENT ABSOLUTE CREATININE AT LISTING",
        "form": "TCR",
        "var_start_date": "1999-10-25 00:00:00",
        "var_end_date": "2007-01-01 00:00:00",
        "form_section": "CLINICAL INFORMATION",
        "data_type": "NUM",
        "sas_analysis_format": "",
        "comment": "Collection ended 1/1/07 for Lung (see INIT_CREAT & END_CREAT instead) ",
        "observed_values": [
            "1.20",
            "0.60",
            ".",
            "2.30",
            "1.80",
            "1.10",
            "0.70"
        ]
    },

Based on the description and observed values, provide YAML-formatted mappings as follows:

Variable_name: {variable_name}
HPO_term: HP:XXXXXXX
HPO_label: {hpo_label}
function: {condition function}

If a variable cannot be mapped to any HPO term, return an empty YAML object.

The condition function is a Python expression that will be used to assign the HPO term (or not)
based on the variable observed in the patient. For example:
x > 1.2
might be used to assign an HPO term if the observed value of the variable is greater
than 1.2. and 

x == "True" or x == "Yes" or x == "1"
might be used to assign an HPO term if the observed value of the variable is "True", 
"Yes", or "1".
'''

    # loop through data_dict
    for item in data_dict.items():

        prompt = f'''Here is the description of the clinical variable:\n{item}\n
        Based on the description, provide YAML-formatted mapping to HPO as follows. 

        Variable_name: variable_name  
        HPO_term: HP:XXXXXXX
        HPO_label: HPO label
        function: condition_function        

        DO NOT EXPLAIN YOUR ANSWER. Just provide this YAML. Provide empty YAML if no
        mapping to an HPO term is possible.
        '''

        # RAG to find most relevant HPO terms
        kb_results = list(
            db.search(item[1]['description'], relevance_factor=0.99, limit=5, **kwargs)
        )
        if prefix:  # filter for prefixes of interest
            filtered_kb_results = []
            for this_result in kb_results:
                for p in prefix:
                    if this_result[0]['original_id'].startswith(p):
                        filtered_kb_results.append(this_result)
            kb_results = filtered_kb_results

        if len(kb_results) > 0:
            prompt = prompt + "\nHere are some HPO terms that might be relevant\n" + "\n".join(
                [r[0]['original_id'] + ' ' + r[0]['label'] for r in kb_results])

        try:
            resp = model_obj.prompt(system=system, prompt=prompt)
            print(f"{prompt}\n\n\n====\n{resp}")
        except openai.error.InvalidRequestError:
            print(f"Error with prompt: {prompt}")
            continue

        # add to yaml_content
        yaml_content.append(resp)
        pass

    # Write the YAML content to a file
    with open(output_file, 'w') as yaml_file:
        yaml.dump(yaml_content, yaml_file, sort_keys=False)

    click.echo(f'Mappings have been saved to {output_file}')


main.add_command(make_unos_mapping_logic)


def parse_html_for_columns(file_path):
    """Parse HTML file to extract column names and their types."""
    with open(file_path, 'r', encoding='windows-1252') as file:
        soup = BeautifulSoup(file, 'html.parser')

    columns = []
    date_columns = []  # List to keep track of columns that are dates
    for row in soup.find_all('tr')[1:]:  # Assuming first row is headers
        cells = row.find_all('td')
        col_name = cells[0].text.strip()
        col_type = cells[3].text.strip().lower()
        if 'character' in col_type or col_type or 'mmddyy' in cells[1].text.strip().lower():
            columns.append((col_name, 'object'))
        else:
            columns.append((col_name, 'float64'))

    return columns, date_columns


@click.command(name='ontologize_unos_data')
@click.argument('html_file', type=click.Path(exists=True))
@click.argument('data_file', type=click.Path(exists=True))
@click.argument('mapping_file', type=click.Path(exists=True))
def ontologize_unos_data(html_file, data_file, mapping_file):
    """Parse the TSV data file and map the columns correctly."""
    columns, date_columns = parse_html_for_columns(html_file)
    hpo_mappings = pd.read_excel(mapping_file)
    # loop over the rows and make sure there are no cases where HPO_term is defined but function is not
    for index, row in hpo_mappings.iterrows():
        if pd.notna(row['HPO_term']) and pd.isna(row['function']):
            raise ValueError(f"Function missing for HPO term {row['HPO_term']}")

    col_names = [col[0] for col in columns]
    col_types = {col[0]: col[1] for col in columns if col[1] != 'datetime64'}

    df = pd.read_csv(data_file, sep='\t', names=col_names, dtype=col_types,
                     na_values='.', parse_dates=date_columns,
                     infer_datetime_format=True)
    print(df.head())  # Print the first few rows of the DataFrame for verification
    print(
        hpo_mappings.head())  # Print the first few rows of the HPO mappings DataFrame for verification

    patient_hpo_terms = []

    # Loop through each pt_row in the dataframe
    for index, pt_row in tqdm(df.iterrows(), "Mapping pt data to HPO terms"):
        patient_terms = set()  # Set to store unique HPO terms for each patient

        # Loop through each mapping
        for _, mapping in hpo_mappings.iterrows():
            this_variable = mapping['Variable_name']

            if this_variable not in pt_row:
                raise RuntimeError(f"Variable {this_variable} not found in data file")
            if pd.notna(pt_row[this_variable]) and pd.notna(mapping['HPO_term']):

                this_pt_val = pt_row[this_variable]
                # ['CHAR(1)', 'C', 'N', 'NUM', 'CHAR(7)', 'CHAR(2)', nan, 'CHAR(15)', 'CHAR(4)']
                if mapping['data_type'] in ['NUM', 'N']:
                    # coerce into number
                    this_pt_val = float(this_pt_val)
                elif mapping['data_type'] in \
                    ['CHAR(1)', 'C', 'CHAR(7)', 'CHAR(2)', 'CHAR(15)', 'CHAR(4)']:
                    # surround this_pt_val with quotes
                    if not this_pt_val.startswith("'") or not this_pt_val.startswith("\""):
                        this_pt_val = f"'{this_pt_val}'"
                else:
                    raise RuntimeError(f"Not sure what to do with this mapping {mappings} with data type {mapping['data_type']}")

                try:
                    # Prepare the function from the mapping
                    function = mapping['function'].replace('x', str(this_pt_val))

                    # Evaluate the function and if true, add the HPO term to the set
                    if eval(function):
                        patient_terms.add(mapping['HPO_term'])
                except Exception as e:
                    print(f"Error evaluating function for {this_variable}: {str(e)}")

        patient_hpo_terms.append(patient_terms)  # Add the set of HPO terms for this patient to the list

    # Now patient_hpo_terms contains a list of sets, each set contains the HPO terms for one patient
    print(patient_hpo_terms)  # Optionally print or handle the HPO terms as needed


main.add_command(ontologize_unos_data)

if __name__ == "__main__":
    main()
