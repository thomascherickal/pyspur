# inspired by https://github.com/google-deepmind/gemma/blob/main/colabs/gsm8k_eval.ipynb
import asyncio
import os
import re
from typing import Any, Callable, Dict, List, Optional
import importlib.util
import pandas as pd
import yaml
from app.evals.common import (
    EQUALITY_TEMPLATE,
    MULTILINGUAL_ANSWER_PATTERN_TEMPLATE,
    MULTILINGUAL_ANSWER_REGEXES,
    QUERY_TEMPLATE_MULTICHOICE,
    extract_answer_with_regex,
    normalize_extracted_answer,
)
from app.nodes.llm.string_output_llm import (
    StringOutputLLMNode,
    StringOutputLLMNodeConfig,
    StringOutputLLMNodeInput,
)
from datasets import Dataset, load_dataset
from jinja2 import Template


def find_numbers(x: str) -> List[str]:
    """Finds all numbers in a string."""
    numbers = re.compile(
        r"-?[\d,]*\.?\d+",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    ).findall(x)
    return numbers


def find_number(x: str, answer_delimiter: str = "The answer is") -> str:
    """Finds the most relevant number in a string."""
    if answer_delimiter in x:
        answer = x.split(answer_delimiter)[-1]
        numbers = find_numbers(answer)
        if numbers:
            return numbers[0]
    # In general, select the last number in the string.
    numbers = find_numbers(x)
    if numbers:
        return numbers[-1]
    return ""


def maybe_remove_comma(x: str) -> str:
    # Example: 5,600 -> 5600
    return x.replace(",", "")


def extract_mcq_answer(response_text: str, language: str = "EN") -> str:
    """Extracts the answer letter (e.g., A, B, C, D) from multiple-choice responses."""
    # Define regex patterns for different languages if needed
    answer_regex = r"\b[A-D]\b"  # Matches standalone A, B, C, or D
    match = re.search(answer_regex, response_text.strip().upper())
    if match:
        return match.group(0)
    else:
        # If no match is found, attempt to extract from the first line
        ans = response_text.strip().split("\n")[0]
        ans = re.sub(r"[^A-D]", "", ans.upper())
        return ans


def load_dataset_by_name(
    dataset_name: str,
    split: Optional[str] = "test",
    subset: Optional[str] = None,
    process_docs: Optional[Callable[[Dataset], Dataset]] = None,
) -> Dataset:
    """Loads a dataset by name or from a CSV file and returns the specified split."""
    if dataset_name.endswith(".csv"):
        # Load dataset from CSV file
        dataset = pd.read_csv(dataset_name)
        # Convert pandas DataFrame to Hugging Face Dataset
        from datasets import Dataset

        dataset = Dataset.from_pandas(dataset)
    else:
        if subset:
            dataset = load_dataset(dataset_name, subset, cache_dir="/tmp")
        else:
            dataset = load_dataset(dataset_name, cache_dir="/tmp")
        if split:
            dataset = dataset[split]
    if process_docs is not None:
        dataset = process_docs(dataset)
    return dataset


# https://github.com/EleutherAI/lm-evaluation-harness/blob/1185e89a044618b5adc6f0b9363b629a19fffdc4/lm_eval/utils.py#L402
def ignore_constructor(loader, node):
    return node


# https://github.com/EleutherAI/lm-evaluation-harness/blob/1185e89a044618b5adc6f0b9363b629a19fffdc4/lm_eval/utils.py#L406
def import_function(loader, node):
    function_name = loader.construct_scalar(node)
    yaml_path = os.path.dirname(loader.name)

    *module_name, function_name = function_name.split(".")
    if isinstance(module_name, list):
        module_name = ".".join(module_name)
    module_path = os.path.normpath(os.path.join(yaml_path, "{}.py".format(module_name)))

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    function = getattr(module, function_name)
    return function


# https://github.com/EleutherAI/lm-evaluation-harness/blob/1185e89a044618b5adc6f0b9363b629a19fffdc4/lm_eval/utils.py#L423
def load_yaml_config(yaml_path=None, yaml_config=None, yaml_dir=None, mode="full"):
    if mode == "simple":
        constructor_fn = ignore_constructor
    elif mode == "full":
        constructor_fn = import_function

    # Add the import_function constructor to the YAML loader
    yaml.add_constructor("!function", constructor_fn)
    if yaml_config is None:
        with open(yaml_path, "rb") as file:
            yaml_config = yaml.full_load(file)

    if yaml_dir is None:
        yaml_dir = os.path.dirname(yaml_path)

    assert yaml_dir is not None

    if "include" in yaml_config:
        include_path = yaml_config["include"]
        del yaml_config["include"]

        if isinstance(include_path, str):
            include_path = [include_path]

        # Load from the last one first
        include_path.reverse()
        final_yaml_config = {}
        for path in include_path:
            # Assumes that path is a full path.
            # If not found, assume the included yaml
            # is in the same dir as the original yaml
            if not os.path.isfile(path):
                path = os.path.join(yaml_dir, path)

            try:
                included_yaml_config = load_yaml_config(yaml_path=path, mode=mode)
                final_yaml_config.update(included_yaml_config)
            except Exception as ex:
                # If failed to load, ignore
                raise ex

        final_yaml_config.update(yaml_config)
        return final_yaml_config
    return yaml_config


def generate_input_prompt(problem, doc_to_text, preamble, prompt):
    """Generates the input prompt for the model."""

    doc_to_text_template = Template(doc_to_text)
    question_text = doc_to_text_template.render(**problem)

    full_prompt = f"{preamble}\n\n{prompt}\n{question_text}"
    return full_prompt.strip()


async def check_equality(expr1: str, expr2: str) -> bool:
    """
    Check if two expressions are equal by using the call_model function.

    Args:
        expr1 (str): The first expression.
        expr2 (str): The second expression.

    Returns:
        bool: True if expressions are equal, False otherwise.
    """
    prompt = EQUALITY_TEMPLATE % {"expression1": expr1, "expression2": expr2}
    response = await call_model(prompt)
    return response.lower().strip() == "yes"


async def call_model(full_prompt):
    """Calls the LLM model using StringOutputLLMNode."""
    # Instantiate the StringOutputLLMNode with the desired configuration
    basic_llm_node = StringOutputLLMNode(
        config=StringOutputLLMNodeConfig(
            llm_name="gpt-4o-mini",
            max_tokens=256,
            temperature=0.0,
            json_mode=False,
            system_prompt="",  # You can set this if needed
            few_shot_examples=None,  # Add few-shot examples if required
        )
    )
    # Create the input data
    basic_input = StringOutputLLMNodeInput(user_message=full_prompt)
    # Call the node to get the output
    basic_output = await basic_llm_node(basic_input)
    return basic_output.assistant_message


def format_multichoice_question(choices_dict):
    return QUERY_TEMPLATE_MULTICHOICE.format(**choices_dict)


def extract_answer(response_text, answer_extraction: Dict[str, Any]):
    """Extracts the answer from the response text based on extraction logic."""
    extraction_method = answer_extraction.get("method", "default")
    if extraction_method == "find_number":
        answer = maybe_remove_comma(find_number(response_text))
        return answer
    elif extraction_method == "mcq":
        # Use MULTILINGUAL_ANSWER_REGEXES to extract the answer
        for answer_regex in MULTILINGUAL_ANSWER_REGEXES:
            regex = MULTILINGUAL_ANSWER_PATTERN_TEMPLATE.format(answer_regex)
            match = re.search(regex, response_text)
            if match:
                extracted_answer = normalize_extracted_answer(match.group(1))
                return extracted_answer
        return response_text  # Return empty if no match is found
    elif extraction_method == "math":
        extracted_answer = extract_answer_with_regex(response_text)
        return extracted_answer
    else:
        # Default extraction method
        return response_text.strip()


async def evaluate_answer(
    predicted_answer, ground_truth_answer, evaluation: Dict[str, Any]
):
    """Evaluates if the predicted answer matches the ground truth based on evaluation logic."""
    evaluation_method = evaluation.get("method", "default").lower()
    if evaluation_method == "numeric":
        try:
            correct = float(predicted_answer) == float(ground_truth_answer)
        except:
            correct = predicted_answer == ground_truth_answer
        return correct
    elif evaluation_method == "exact_match":
        return predicted_answer.strip().lower() == ground_truth_answer.strip().lower()
    elif evaluation_method == "mcq":
        # Normalize both answers before comparison
        return (
            normalize_extracted_answer(predicted_answer).strip().upper()
            == normalize_extracted_answer(ground_truth_answer).strip().upper()
        )
    elif evaluation_method == "math":
        print(f"Checking equality between {predicted_answer} and {ground_truth_answer}")
        return await check_equality(predicted_answer, ground_truth_answer)
    else:
        # Default evaluation method
        return predicted_answer == ground_truth_answer


def get_ground_truth_answer(problem, doc_to_target):
    """Extracts the ground truth answer using the doc_to_target template."""
    doc_to_target_template = Template(doc_to_target)
    ground_truth = doc_to_target_template.render(**problem)
    return ground_truth.strip()


async def evaluate_on_dataset(
    dataset: Dataset,
    task_config: Dict[str, Any],
    batch_size: int = 10,
    subject: Optional[str] = None,
    subject_category_mapping: Optional[Dict[str, str]] = None,
    category_correct: Optional[Dict[str, int]] = None,
    category_total: Optional[Dict[str, int]] = None,
) -> dict:
    """Evaluates the model on the given dataset and returns evaluation metrics."""
    # Extract necessary components from task_config
    preamble = task_config.get("preamble", "")
    prompt = task_config.get("prompt", "")
    doc_to_text = task_config.get("doc_to_text", "")
    doc_to_target = task_config.get("doc_to_target", "")
    answer_extraction = task_config.get("answer_extraction", {})
    evaluation = task_config.get("evaluation", {})

    all_responses = {}
    short_responses = {}
    total = len(dataset)
    correct = 0
    task_id = 0

    # Initialize category_correct and category_total if they are None
    if subject_category_mapping and category_correct is None and category_total is None:
        category_correct = {
            category: 0 for category in set(subject_category_mapping.values())
        }
        category_total = {
            category: 0 for category in set(subject_category_mapping.values())
        }

    for batch in dataset.iter(batch_size=batch_size):
        transformed_batch = [
            dict(zip(batch.keys(), values)) for values in zip(*batch.values())
        ]
        full_prompts = [
            generate_input_prompt(problem, doc_to_text, preamble, prompt)
            for problem in transformed_batch
        ]
        # Call the model on all prompts in the batch concurrently
        responses = await asyncio.gather(
            *[call_model(prompt) for prompt in full_prompts]
        )
        for idx, problem in enumerate(transformed_batch):
            response_text = responses[idx]
            all_responses[task_id] = response_text
            predicted_answer = extract_answer(response_text, answer_extraction)
            short_responses[task_id] = predicted_answer
            ground_truth_answer = get_ground_truth_answer(problem, doc_to_target)
            is_correct = await evaluate_answer(
                predicted_answer, ground_truth_answer, evaluation
            )
            correct += int(is_correct)

            # Category-wise aggregation
            if subject_category_mapping:
                if "subject" in problem:
                    subject = problem["subject"]
                # Use provided subject if passed to function
                if not subject and "Subject" in problem:
                    subject = problem["Subject"]
                category = subject_category_mapping.get(subject, "other")
                category_total[category] += 1
                if is_correct:
                    category_correct[category] += 1

            print(f"task_id {task_id}")
            print(f"Predicted answer: {predicted_answer}")
            print(f"Ground truth answer: {ground_truth_answer}")
            print(f"Correct: {is_correct}")
            print("=" * 40)
            task_id += 1
    # Calculate accuracy
    accuracy = correct / total
    # Aggregate metrics in a dictionary
    metrics = {
        "total_samples": total,
        "correct_predictions": correct,
        "accuracy": accuracy,
        "all_responses": all_responses,
        "short_responses": short_responses,
    }
    if subject_category_mapping:
        metrics["category_correct"] = category_correct
        metrics["category_total"] = category_total
        metrics["category_accuracy"] = {
            category: (
                category_correct[category] / category_total[category]
                if category_total[category] > 0
                else 0
            )
            for category in category_correct
        }
    return metrics


async def evaluate_model_on_dataset(
    task_config: dict,
    batch_size: int = 10,
    num_samples: Optional[int] = None,  # Added `num_samples` parameter
) -> dict:
    """Evaluates the model on the specified dataset and returns evaluation metrics."""
    # Extract configurations from task_config
    dataset_name = task_config.get("dataset_name")
    dataset_split = task_config.get("dataset_split", "test")
    dataset_subsets = task_config.get("dataset_subsets", None)
    subject_category_mapping = task_config.get("subject_category_mapping", None)
    process_docs = task_config.get("process_docs", None)

    # Ensure dataset_name is provided
    if not dataset_name:
        raise ValueError("dataset_name must be provided in task_config.")

    # Initialize category_correct and category_total if mapping exists
    if subject_category_mapping:
        category_correct = {
            category: 0 for category in set(subject_category_mapping.values())
        }
        category_total = {
            category: 0 for category in set(subject_category_mapping.values())
        }
    else:
        category_correct = None
        category_total = None

    # Check if dataset_subsets is a list
    if isinstance(dataset_subsets, list):
        total_correct = 0
        total_samples = 0
        subset_metrics = {}
        for subset in dataset_subsets:
            print(f"Evaluating subset: {subset}")
            # Load the dataset for the current subset
            dataset = load_dataset_by_name(
                dataset_name, dataset_split, subset, process_docs
            )
            # Subsample the dataset if num_samples is specified
            if num_samples is not None:
                dataset = dataset.shuffle(seed=42).select(
                    range(min(num_samples, len(dataset)))
                )
            metrics = await evaluate_on_dataset(
                dataset,
                task_config,
                batch_size,
                subject=subset,
                subject_category_mapping=subject_category_mapping,
                category_correct=category_correct,
                category_total=category_total,
            )
            subset_metrics[subset] = metrics
            total_correct += metrics["correct_predictions"]
            total_samples += metrics["total_samples"]
        # Calculate overall accuracy across all subsets
        overall_accuracy = total_correct / total_samples if total_samples > 0 else 0
        results = {
            "total_samples": total_samples,
            "correct_predictions": total_correct,
            "accuracy": overall_accuracy,
            "subset_metrics": subset_metrics,
        }
        if subject_category_mapping:
            # Add category-wise accuracy
            category_accuracy = {
                category: (
                    category_correct[category] / category_total[category]
                    if category_total[category] > 0
                    else 0
                )
                for category in category_correct
            }
            results["category_accuracy"] = category_accuracy
        return results
    else:
        # Handle the case where dataset_subsets is a single subset or None
        # Load the dataset
        dataset = load_dataset_by_name(
            dataset_name, dataset_split, dataset_subsets, process_docs
        )
        # Subsample the dataset if num_samples is specified
        if num_samples is not None:
            dataset = dataset.shuffle(seed=42).select(
                range(min(num_samples, len(dataset)))
            )
        metrics = await evaluate_on_dataset(
            dataset,
            task_config,
            batch_size,
            subject=dataset_subsets,
            subject_category_mapping=subject_category_mapping,
        )
        results = metrics
        if subject_category_mapping:
            results["category_accuracy"] = metrics.get("category_accuracy", {})
        return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate LLM on a dataset.")
    parser.add_argument(
        "--task_config_path",
        type=str,
        default=os.path.join(os.path.dirname(__file__), "tasks", "gpqa.yaml"),
        help="Path to the task configuration YAML file.",
    )
    parser.add_argument(
        "--num_samples",  # Added argument for number of samples
        type=int,
        default=20,
        help="Number of samples to evaluate from the dataset.",
    )
    args = parser.parse_args()

    # Load task configuration from YAML file
    task_config = load_yaml_config(args.task_config_path)
    # Run the evaluation with `num_samples` parameter
    results = asyncio.run(
        evaluate_model_on_dataset(task_config, num_samples=args.num_samples)
    )

    # Print the results
    print("Overall Accuracy:", results.get("accuracy", 0))
    # Print category-wise accuracy if available
    category_accuracy = results.get("category_accuracy", {})
    if category_accuracy:
        print("\nCategory-wise Accuracy:")
        for category, accuracy in category_accuracy.items():
            print(f"Category: {category}, Accuracy: {accuracy:.4f}")
    # Print subset metrics
    task_metrics = results.get("subset_metrics", {})
    for task, metrics in task_metrics.items():
        print(f"\nSubset: {task}, Accuracy: {metrics['accuracy']}")
