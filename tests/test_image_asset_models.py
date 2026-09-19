from src.artifact_assets.models import AssetStatus, AssetValidationProfile


def test_profile_has_safe_source_level_defaults():
    profile = AssetValidationProfile()
    assert profile.low_resolution_edge_px == 300
    assert "PNG" in profile.supported_formats
    assert AssetStatus.VALID.value == "VALID"
