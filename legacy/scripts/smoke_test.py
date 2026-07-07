import py_compile
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import PROJECT_ROOT


def run(command):
    print(f"$ {' '.join(command)}")
    result = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode:
        raise SystemExit(result.returncode)


def compile_python():
    for path in [PROJECT_ROOT / "app.py", *sorted((PROJECT_ROOT / "core").glob("*.py")), *sorted((PROJECT_ROOT / "scripts").glob("*.py"))]:
        py_compile.compile(path, doraise=True)
    print("Python compile: OK")


def main():
    compile_python()
    run([sys.executable, "scripts/check_system.py", "--document-id", "doc_003"])
    run([sys.executable, "scripts/migrate_schema.py"])
    run([sys.executable, "scripts/ingest_pdfs.py", "--document-id", "doc_003"])
    run([sys.executable, "scripts/render_pdf_pages.py", "doc_003", "--page", "1"])
    run([sys.executable, "scripts/extract_return_table_vision.py", "doc_003", "--page", "1", "--mock"])
    run([sys.executable, "scripts/extract_facts_llm.py", "doc_003", "--scope", "profile_terms", "--dry-run"])
    run([sys.executable, "scripts/extract_facts_llm.py", "doc_003", "--all-scopes", "--dry-run"])
    run([sys.executable, "scripts/promote_approved_facts.py", "--dry-run"])
    run([sys.executable, "scripts/export_fund_snapshot.py", "fund_002"])
    run([sys.executable, "scripts/export_performance_csv.py", "--fund-id", "fund_002"])
    run([sys.executable, "scripts/export_source_truth_workbook.py"])
    run([sys.executable, "scripts/export_comparison_workbook.py", "--fund-id", "fund_002", "--fund-id", "fund_003"])
    run([sys.executable, "scripts/detect_conflicts.py"])
    run([sys.executable, "scripts/run_fund_analysis_llm.py", "--fund-id", "fund_002", "--compare-fund-id", "fund_003", "--dry-run"])
    run([sys.executable, "scripts/run_web_research_comparison.py", "--fund-id", "fund_002", "--mock"])
    run([sys.executable, "scripts/internal_tests.py"])
    print("Smoke test passed.")


if __name__ == "__main__":
    main()
