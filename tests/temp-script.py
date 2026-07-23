import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COLLECTION_DIR = PROJECT_ROOT / "collection"
NODES_DIR = COLLECTION_DIR / "nodes"
STATS_DIR = NODES_DIR / "stats"

SOURCE_PATH = COLLECTION_DIR / "n8n-nodes.json"
SUMMARY_PATH = NODES_DIR / "01.n8n-nodes-summary.md"
NODE_NAMES_PATH = NODES_DIR / "02.n8n-nodes-list.txt"
KEYS_CSV_PATH = STATS_DIR / "all-possible-keys.csv"
GROUPS_CSV_PATH = STATS_DIR / "unique-group-combinations.csv"

Node = dict[str, Any]
GroupCombination = tuple[str, ...]


def load_nodes(path: Path) -> list[Node]:
    with path.open(encoding="utf-8") as source_file:
        nodes = json.load(source_file)

    if not isinstance(nodes, list) or not all(isinstance(node, dict) for node in nodes):
        raise ValueError(f"Expected a JSON list of node objects in {path}")

    return nodes


def get_group_combination(node: Node) -> GroupCombination:
    groups = node.get("group", [])
    if isinstance(groups, list):
        return tuple(str(group) for group in groups)
    if groups is None:
        return ()
    return (str(groups),)


def count_group_combinations(nodes: list[Node]) -> Counter[GroupCombination]:
    return Counter(get_group_combination(node) for node in nodes)


def count_properties(nodes: list[Node]) -> Counter[str]:
    return Counter(key for node in nodes for key in node)


def write_group_combinations_csv(
    path: Path,
    group_counts: Counter[GroupCombination],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["groups", "item_count"])
        for groups, count in sorted(group_counts.items()):
            writer.writerow([json.dumps(list(groups), ensure_ascii=False), count])


def write_properties_csv(
    path: Path,
    property_counts: Counter[str],
    total_nodes: int,
) -> None:
    sorted_properties = sorted(
        property_counts.items(),
        key=lambda entry: (-entry[1], entry[0]),
    )

    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["key", "item_count", "usage_rate_percent"])
        for key, count in sorted_properties:
            usage_rate = count / total_nodes * 100
            writer.writerow([key, count, f"{usage_rate:.2f}"])


def write_node_names(path: Path, nodes: list[Node]) -> None:
    names = sorted(str(node.get("name", "N/A")) for node in nodes)
    path.write_text("".join(f"{name}\n" for name in names), encoding="utf-8")


def build_summary(
    nodes: list[Node],
    property_counts: Counter[str],
    group_counts: Counter[GroupCombination],
) -> str:
    total_nodes = len(nodes)
    unique_names = {str(node.get("name", "N/A")) for node in nodes}
    all_properties = set(property_counts)
    always_present_properties = sorted(
        key for key, count in property_counts.items() if count == total_nodes
    )
    individual_group_types = {
        group for combination in group_counts for group in combination
    }

    max_property_count = max(len(node) for node in nodes)
    nodes_with_max_properties = sorted({
        str(node.get("name", "N/A"))
        for node in nodes
        if len(node) == max_property_count
    })
    nodes_with_all_properties = [
        node for node in nodes if all_properties.issubset(node)
    ]

    lines = [
        "# n8n Node Summary",
        "",
        "This summary is generated automatically from `n8n-nodes.json`. "
        "Property statistics cover only the top-level keys of each node object.",
        "",
        "## General statistics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total node records | {total_nodes} |",
        f"| Unique node types (`name`) | {len(unique_names)} |",
        f"| Additional records sharing an existing `name` | {total_nodes - len(unique_names)} |",
        f"| Potential top-level node properties | {len(all_properties)} |",
        f"| Maximum properties on a single node | {max_property_count} |",
        f"| Properties present on every node | {len(always_present_properties)} |",
        f"| Unique individual group types | {len(individual_group_types)} |",
        f"| Unique group combinations | {len(group_counts)} |",
        "",
        "## Group combinations",
        "",
        "| Group combination | Node count |",
        "| --- | ---: |",
    ]

    for groups, count in sorted(group_counts.items()):
        group_label = " + ".join(groups) if groups else "Ungrouped"
        lines.append(f"| `{group_label}` | {count} |")

    lines.extend([
        "",
        "## Property information",
        "",
        f"The {len(always_present_properties)} properties present on every node are: "
        + ", ".join(f"`{key}`" for key in always_present_properties)
        + ".",
        "",
        f"The nodes with the maximum of {max_property_count} properties are: "
        + ", ".join(f"`{name}`" for name in nodes_with_max_properties)
        + ".",
        "",
    ])

    if nodes_with_all_properties:
        matching_names = sorted({
            str(node.get("name", "N/A")) for node in nodes_with_all_properties
        })
        lines.append(
            f"Nodes containing all {len(all_properties)} potential properties: "
            + ", ".join(f"`{name}`" for name in matching_names)
            + "."
        )
    else:
        lines.append(
            f"No single node contains all {len(all_properties)} potential properties."
        )

    lines.extend([
        "",
        "## Detailed datasets",
        "",
        "- [Complete node list](02.n8n-nodes-list.txt)",
        "- [All potential properties](stats/all-possible-keys.csv)",
        "- [Group combinations](stats/unique-group-combinations.csv)",
        "- [Authentication types](auth/authentication-types.txt)",
        "- [Credential types](auth/cred-types.txt)",
        "- [Predefined credential types](auth/predefined-cred-types.txt)",
        "",
    ])

    return "\n".join(lines)


def main() -> None:
    nodes = load_nodes(SOURCE_PATH)
    if not nodes:
        raise ValueError(f"No node records found in {SOURCE_PATH}")

    STATS_DIR.mkdir(parents=True, exist_ok=True)

    property_counts = count_properties(nodes)
    group_counts = count_group_combinations(nodes)

    write_properties_csv(KEYS_CSV_PATH, property_counts, len(nodes))
    write_group_combinations_csv(GROUPS_CSV_PATH, group_counts)
    write_node_names(NODE_NAMES_PATH, nodes)
    SUMMARY_PATH.write_text(
        build_summary(nodes, property_counts, group_counts),
        encoding="utf-8",
    )

    unique_node_count = len({str(node.get("name", "N/A")) for node in nodes})
    print(f"Processed {len(nodes)} node records ({unique_node_count} unique names).")
    print(f"Generated reports in {NODES_DIR.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
