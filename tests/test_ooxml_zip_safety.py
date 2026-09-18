import zipfile
from pathlib import Path
from src.artifact_evaluation.common import OoxmlZipSafetyPolicy, zip_is_safe

def make_zip(path: Path, entries, expected="word/document.xml"):
    with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as z:
        for name,data in entries: z.writestr(name,data)
    return zip_is_safe(path, expected, OoxmlZipSafetyPolicy(max_members=4,max_member_bytes=50,max_total_bytes=80,max_compression_ratio=10,chunk_size=8))

def test_zip_policy_accepts_small_normal_ooxml(tmp_path):
    assert make_zip(tmp_path/"ok.docx", [("word/document.xml",b"ok")])[0]

def test_zip_policy_rejects_resource_and_path_attacks(tmp_path):
    assert make_zip(tmp_path/"many.docx", [("word/document.xml",b"x")]+[(f"x{i}",b"x") for i in range(4)])[1]=="too_many_members"
    assert make_zip(tmp_path/"large.docx", [("word/document.xml",b"x"*51)])[1]=="member_too_large"
    assert make_zip(tmp_path/"total.docx", [("word/document.xml",b"x"*40),("x",b"x"*41)])[1]=="total_uncompressed_too_large"
    assert make_zip(tmp_path/"path.docx", [("word/document.xml",b"x"),("../x",b"x")])[1]=="unsafe_archive_member"

def test_zip_policy_rejects_ratio_duplicate_and_corruption(tmp_path):
    ratio=tmp_path/"ratio.docx"
    with zipfile.ZipFile(ratio,"w",zipfile.ZIP_DEFLATED) as z: z.writestr("word/document.xml",b"0"*50)
    assert zip_is_safe(ratio,"word/document.xml",OoxmlZipSafetyPolicy(max_members=4,max_member_bytes=50,max_total_bytes=80,max_compression_ratio=2,chunk_size=8))[1]=="suspicious_compression_ratio"
    with zipfile.ZipFile(tmp_path/"dup.docx","w") as z: z.writestr("word/document.xml",b"x");z.writestr("WORD/document.xml",b"x")
    assert zip_is_safe(tmp_path/"dup.docx","word/document.xml")[1]=="duplicate_member"
    bad=tmp_path/"bad.docx";bad.write_bytes(b"not a zip");assert zip_is_safe(bad,"word/document.xml")[1]=="corrupt_or_truncated_zip"
