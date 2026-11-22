from brandai.onboarding.visual import VisualSignals, build_agency_visual_system, contrast_ratio


def test_agency_visual_system_assigns_roles_and_wcag_decisions():
    signals = VisualSignals(["#2457D6", "#17223B", "#F4B942"], ["Heebo"],
        {"hebrew": "Heebo", "english": "Inter"}, ["https://brand.test/logo.svg"], "text-free studio scene")
    system = build_agency_visual_system(signals)
    assert system.color_roles["usage_ratio"] == {"primary": 60, "secondary": 30, "accent": 10}
    assert system.contrast["primary"]["normal_text_aa"] is True
    assert system.typography["base_px"] == 16
    assert system.logo_geometry["safe_zone_percent"] == 6
    assert system.flux_constraints["text_free"] is True
    assert round(contrast_ratio("#000000", "#FFFFFF"), 1) == 21.0
