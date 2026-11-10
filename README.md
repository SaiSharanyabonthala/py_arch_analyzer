\# Python Architecture \& Dependency Graph Analyzer



\[!\[Architecture Check](https://github.com/SaiSharanyabonthala/py\_arch\_analyzer/actions/workflows/arch\_check.yml/badge.svg)](https://github.com/SaiSharanyabonthala/py\_arch\_analyzer/actions/workflows/arch\_check.yml)

\[!\[Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

\[!\[License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)



A static analysis tool that inspects Python source files using Abstract Syntax Trees (AST) to detect circular import loops, calculate coupling metrics, and render interactive dependency graphs.



\---



\## Features



\- \*\*Zero-Execution AST Parsing:\*\* Statically scans code without executing untrusted files.

\- \*\*Circular Import Detection:\*\* Identifies cycles via Tarjan's Strongly Connected Components algorithm.

\- \*\*Coupling \& Instability Metrics:\*\*

&#x20; - Afferent Coupling ($Ca$): Incoming dependencies.

&#x20; - Efferent Coupling ($Ce$): Outgoing dependencies.

&#x20; - Instability ($I$): $I = \\frac{Ce}{Ca + Ce}$.

\- \*\*Interactive HTML Graph:\*\* Uses PyVis to generate physics-based network diagrams (`--html`).

\- \*\*CI/CD Quality Gate:\*\* Supports `--fail-on-cycle` for GitHub Actions pull request blocking.



\---



\## Installation



```bash

git clone \[https://github.com/SaiSharanyabonthala/py\_arch\_analyzer.git](https://github.com/SaiSharanyabonthala/py\_arch\_analyzer.git)

cd py\_arch\_analyzer

pip install -e .

