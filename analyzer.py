import ast
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Tuple

import click
import networkx as nx
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


# ----------------------------------------------------------------------
# 1. AST Visitor: Extract imports & relative levels
# ----------------------------------------------------------------------
class ImportVisitor(ast.NodeVisitor):
    def __init__(self):
        # Raw imports: (imported_module, relative_level)
        # e.g., from ..foo import bar -> ('foo', 2)
        #       import os             -> ('os', 0)
        self.raw_imports: List[Tuple[str, int]] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.raw_imports.append((alias.name, 0))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        mod = node.module or ""
        level = node.level  # 0: absolute, 1: current pkg, 2: parent pkg...
        self.raw_imports.append((mod, level))
        self.generic_visit(node)


# ----------------------------------------------------------------------
# 2. File Indexing & Canonical Module Resolution
# ----------------------------------------------------------------------
class CodebaseContext:
    def __init__(self, root_path: Path):
        self.root_path = root_path.resolve()
        # Maps canonical dot path (e.g., 'pkg.utils') -> Path object
        self.module_to_file: Dict[str, Path] = {}
        # Maps Path object -> canonical dot path
        self.file_to_module: Dict[Path, str] = {}
        self._index_repo()

    def _index_repo(self):
        for path in self.root_path.rglob("*.py"):
            # Exclude virtual environments, cache, and hidden directories
            parts = path.relative_to(self.root_path).parts
            if any(p.startswith(".") or p in {"venv", ".venv", "__pycache__", "build", "dist"} for p in parts):
                continue

            rel_parts = list(parts)
            # Remove .py suffix
            rel_parts[-1] = rel_parts[-1][:-3]
            
            # Normalize '__init__' to the package name itself
            if rel_parts[-1] == "__init__":
                rel_parts.pop()

            if not rel_parts:
                module_name = self.root_path.name
            else:
                module_name = ".".join(rel_parts)

            self.module_to_file[module_name] = path
            self.file_to_module[path] = module_name

    def resolve_target(self, current_module: str, imported_mod: str, level: int) -> str | None:
        """
        Converts an import statement into a canonical module if it resides in the project.
        """
        curr_parts = current_module.split(".") if current_module else []

        if level > 0:
            # Relative import resolution
            # level=1 -> same package; level=2 -> parent package, etc.
            base_parts = curr_parts[:-level] if level <= len(curr_parts) else []
            if imported_mod:
                candidate = ".".join(base_parts + imported_mod.split("."))
            else:
                candidate = ".".join(base_parts)
        else:
            # Absolute import resolution
            candidate = imported_mod

        # Exact match
        if candidate in self.module_to_file:
            return candidate

        # Partial match (e.g. from utils import helper where helper is a function inside utils.py)
        while candidate:
            if candidate in self.module_to_file:
                return candidate
            if "." not in candidate:
                break
            candidate = candidate.rsplit(".", 1)[0]

        return None


# ----------------------------------------------------------------------
# 3. Dependency Graph Builder
# ----------------------------------------------------------------------
class DependencyAnalyzer:
    def __init__(self, context: CodebaseContext):
        self.context = context
        self.graph = nx.DiGraph()

    def build_graph(self):
        for module_name, file_path in self.context.module_to_file.items():
            self.graph.add_node(module_name)
            
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(file_path))
            except (SyntaxError, UnicodeDecodeError):
                continue

            visitor = ImportVisitor()
            visitor.visit(tree)

            for target_raw, level in visitor.raw_imports:
                resolved = self.context.resolve_target(module_name, target_raw, level)
                if resolved and resolved != module_name:
                    # module_name depends on resolved
                    self.graph.add_edge(module_name, resolved)

    def find_cycles(self) -> List[List[str]]:
        """
        Finds circular import cycles using Tarjan's Strongly Connected Components algorithm.
        """
        sccs = list(nx.strongly_connected_components(self.graph))
        # Keep components with > 1 node, or single nodes that self-reference
        cycles = [
            list(scc) for scc in sccs 
            if len(scc) > 1 or (len(scc) == 1 and self.graph.has_edge(list(scc)[0], list(scc)[0]))
        ]
        return cycles

    def compute_metrics(self) -> List[dict]:
        """
        Calculates:
          - Ca (Afferent Coupling): Incoming edges (incoming dependents)
          - Ce (Efferent Coupling): Outgoing edges (outgoing dependencies)
          - Instability (I) = Ce / (Ca + Ce)
        """
        results = []
        for node in self.graph.nodes():
            ca = self.graph.in_degree(node)
            ce = self.graph.out_degree(node)
            instability = ce / (ca + ce) if (ca + ce) > 0 else 0.0
            
            results.append({
                "module": node,
                "ca": ca,
                "ce": ce,
                "instability": instability
            })

        # Sort by most unstable first
        return sorted(results, key=lambda x: x["instability"], reverse=True)


# ----------------------------------------------------------------------
# 4. Rich Terminal Reporting & CLI
# ----------------------------------------------------------------------
# Change the CLI function signature to accept the flag:
@click.command()
@click.argument("directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--fail-on-cycle",
    is_flag=True,
    default=False,
    help="Exit with non-zero status if circular dependencies are detected (for CI/CD)."
)
def main(directory: Path, fail_on_cycle: bool):
    """Analyze Python codebase architecture and dependency coupling."""
    console.print(Panel(f"[bold cyan]Scanning directory:[/bold cyan] {directory.resolve()}"))

    context = CodebaseContext(directory)
    if not context.module_to_file:
        console.print("[yellow]No Python modules found.[/yellow]")
        sys.exit(0)

    analyzer = DependencyAnalyzer(context)
    analyzer.build_graph()

    # 1. Circular Import Loops
    cycles = analyzer.find_cycles()
    if cycles:
        console.print(Panel(f"[bold red]Circular Dependencies Detected: {len(cycles)}[/bold red]", expand=False))
        for idx, cycle in enumerate(cycles, 1):
            console.print(f"[red]{idx}.[/red] " + " [bold]⇄[/bold] ".join(cycle))
    else:
        console.print("[bold green]✔ No circular dependencies found.[/bold green]")

    console.print()

    # 2. Coupling & Instability Table
    metrics = analyzer.compute_metrics()
    table = Table(title="Architectural Coupling & Instability Metric (I = Ce / (Ca + Ce))")
    table.add_column("Module", style="white")
    table.add_column("Ca (Afferent)", justify="center", style="green")
    table.add_column("Ce (Efferent)", justify="center", style="yellow")
    table.add_column("Instability (I)", justify="center")

    for row in metrics:
        i_score = row["instability"]
        color = "cyan" if i_score <= 0.3 else ("yellow" if i_score <= 0.7 else "magenta")
        table.add_row(
            row["module"],
            str(row["ca"]),
            str(row["ce"]),
            f"[{color}]{i_score:.2f}[/{color}]"
        )

    console.print(table)

    # Trigger CI Failure if flag is active and cycles exist
    if fail_on_cycle and cycles:
        console.print("[bold red]CI Failure: Circular dependencies are not permitted.[/bold red]")
        sys.exit(1)