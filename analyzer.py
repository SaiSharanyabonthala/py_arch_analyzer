import ast
import os
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

import click
import networkx as nx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


# ----------------------------------------------------------------------
# 1. AST Visitor: Extract imports & relative levels
# ----------------------------------------------------------------------
class ImportVisitor(ast.NodeVisitor):
    def __init__(self):
        self.raw_imports: List[Tuple[str, int]] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.raw_imports.append((alias.name, 0))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        mod = node.module or ""
        level = node.level
        self.raw_imports.append((mod, level))
        self.generic_visit(node)


# ----------------------------------------------------------------------
# 2. File Indexing & Canonical Module Resolution
# ----------------------------------------------------------------------
class CodebaseContext:
    def __init__(self, root_path: Path):
        self.root_path = root_path.resolve()
        self.module_to_file: Dict[str, Path] = {}
        self.file_to_module: Dict[Path, str] = {}
        self._index_repo()

    def _index_repo(self):
        root_is_pkg = (self.root_path / "__init__.py").exists()

        for path in self.root_path.rglob("*.py"):
            parts = path.relative_to(self.root_path).parts
            if any(p.startswith(".") or p in {"venv", ".venv", "__pycache__", "build", "dist"} for p in parts):
                continue

            rel_parts = list(parts)
            rel_parts[-1] = rel_parts[-1][:-3]
            
            if rel_parts[-1] == "__init__":
                rel_parts.pop()

            if root_is_pkg:
                rel_parts = [self.root_path.name] + rel_parts

            if not rel_parts:
                module_name = self.root_path.name
            else:
                module_name = ".".join(rel_parts)

            self.module_to_file[module_name] = path
            self.file_to_module[path] = module_name

    def resolve_target(self, current_module: str, imported_mod: str, level: int) -> str | None:
        curr_parts = current_module.split(".") if current_module else []

        if level > 0:
            base_parts = curr_parts[:-level] if level <= len(curr_parts) else []
            if imported_mod:
                candidate = ".".join(base_parts + imported_mod.split("."))
            else:
                candidate = ".".join(base_parts)
        else:
            candidate = imported_mod

        if candidate in self.module_to_file:
            return candidate

        while candidate:
            if candidate in self.module_to_file:
                return candidate
            if "." not in candidate:
                break
            candidate = candidate.rsplit(".", 1)[0]

        return None


# ----------------------------------------------------------------------
# 3. Dependency Graph Analyzer & HTML Visualizer
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
                    self.graph.add_edge(module_name, resolved)

    def find_cycles(self) -> List[List[str]]:
        sccs = list(nx.strongly_connected_components(self.graph))
        cycles = [
            list(scc) for scc in sccs 
            if len(scc) > 1 or (len(scc) == 1 and self.graph.has_edge(list(scc)[0], list(scc)[0]))
        ]
        return cycles

    def compute_metrics(self) -> List[dict]:
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

        return sorted(results, key=lambda x: x["instability"], reverse=True)

    def export_html(self, output_file: Path, cycles: List[List[str]]):
        from pyvis.network import Network

        net = Network(height="750px", width="100%", bgcolor="#1a1a1a", font_color="white", directed=True)
        cycle_nodes: Set[str] = {node for cycle in cycles for node in cycle}

        for node in self.graph.nodes():
            ca = self.graph.in_degree(node)
            ce = self.graph.out_degree(node)
            is_cyclic = node in cycle_nodes

            color = "#ff4d4d" if is_cyclic else "#4da6ff"
            title = f"Module: {node}<br>Afferent (Ca): {ca}<br>Efferent (Ce): {ce}<br>Status: {'CIRCULAR' if is_cyclic else 'Normal'}"
            size = 15 + (ca * 5)

            net.add_node(node, label=node, title=title, color=color, size=size)

        for src, dst in self.graph.edges():
            is_cycle_edge = (src in cycle_nodes) and (dst in cycle_nodes)
            edge_color = "#ff4d4d" if is_cycle_edge else "#888888"
            width = 3 if is_cycle_edge else 1
            net.add_edge(src, dst, color=edge_color, width=width)

        net.set_options("""
        var options = {
          "physics": {
            "forceAtlas2Based": {
              "gravitationalConstant": -50,
              "centralGravity": 0.01,
              "springLength": 100,
              "springConstant": 0.08
            },
            "solver": "forceAtlas2Based"
          }
        }
        """)
        net.write_html(str(output_file))
        console.print(f"[bold green]✔ Interactive architecture graph exported to:[/bold green] {output_file.resolve()}")


# ----------------------------------------------------------------------
# 4. Rich Terminal Reporting & CLI
# ----------------------------------------------------------------------
@click.command()
@click.argument("directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--fail-on-cycle",
    is_flag=True,
    default=False,
    help="Exit with status code 1 if cycles are found."
)
@click.option(
    "--html",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Export an interactive HTML dependency graph."
)
def main(directory: Path, fail_on_cycle: bool, html: Path | None):
    """Analyze Python codebase architecture and dependency coupling."""
    console.print(Panel(f"[bold cyan]Scanning directory:[/bold cyan] {directory.resolve()}"))

    context = CodebaseContext(directory)
    if not context.module_to_file:
        console.print("[yellow]No Python modules found.[/yellow]")
        sys.exit(0)

    analyzer = DependencyAnalyzer(context)
    analyzer.build_graph()

    cycles = analyzer.find_cycles()
    if cycles:
        console.print(Panel(f"[bold red]Circular Dependencies Detected: {len(cycles)}[/bold red]", expand=False))
        for idx, cycle in enumerate(cycles, 1):
            console.print(f"[red]{idx}.[/red] " + " [bold]⇄[/bold] ".join(cycle))
    else:
        console.print("[bold green]✔ No circular dependencies found.[/bold green]")

    console.print()

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

    if html:
        analyzer.export_html(html, cycles)

    if fail_on_cycle and cycles:
        console.print("[bold red]CI Failure: Circular dependencies are not permitted.[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()