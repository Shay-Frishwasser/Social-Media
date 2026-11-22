import pytest

from brandai.on_demand import PhotoTreatmentPlanner


def test_three_photo_modes_have_explicit_pixel_and_gpu_contracts():
    planner = PhotoTreatmentPlanner()
    original = planner.plan("original_branded", platform="instagram", design_system_version=2)
    enhanced = planner.plan("smart_enhance", platform="instagram", design_system_version=2)
    hero = planner.plan("hero_cutout_scene", platform="instagram", design_system_version=2)
    assert original.preserve_source_pixels and not original.requires_gpu_capabilities
    assert "subject_detection" in enhanced.requires_gpu_capabilities
    assert hero.preserve_source_pixels
    assert hero.requires_gpu_capabilities == {"foreground_segmentation", "image_generation"}
    assert "source RGB pixels remain unchanged" in hero.output_contract["foreground_policy"]


def test_unknown_photo_mode_is_rejected():
    with pytest.raises(ValueError): PhotoTreatmentPlanner().plan("magic", platform="x", design_system_version=1)
