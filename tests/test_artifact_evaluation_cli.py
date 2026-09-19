import subprocess,sys
def test_cli_markdown_and_reports(tmp_path):
 source=tmp_path/"a.md";source.write_text("# T\n\ntext\n",encoding="utf8");out=tmp_path/"out"
 result=subprocess.run([sys.executable,"scripts/evaluate_artifacts.py","--profile","internal-source","--markdown",str(source),"--output-dir",str(out)],capture_output=True,text=True)
 assert result.returncode==0 and (out/"evaluation_report.json").is_file() and (out/"evaluation_report.md").is_file()
