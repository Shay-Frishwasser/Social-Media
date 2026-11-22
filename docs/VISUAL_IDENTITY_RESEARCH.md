# Visual identity research translated into system rules

Verified 2026-11-22. These are versioned design heuristics, not universal aesthetic laws.

## Color hierarchy and accessibility

- USWDS defines primary, secondary, and accent role families and describes a 60/30/10 proportional relationship for non-base colors. BrandAI stores roles and usage ratios rather than an unordered palette.
- WCAG AA contrast is a threshold: 4.5:1 for normal text and 3:1 for large text. BrandAI computes relative luminance, records both white/dark ratios, recommends the safer foreground, and shows a UI warning instead of silently approving an inaccessible pairing.
- Sources: https://designsystem.digital.gov/design-tokens/color/theme-tokens/ and https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum.html

## Hebrew/English typography

- Hebrew and Latin must be tested as a pair at equivalent visual weight, x-height, line height, and hierarchy. Direction belongs to each text block; mixed-script names must not be reversed.
- Heebo extends a Latin design into Hebrew; Noto Sans Hebrew officially supports Hebrew and Latin ranges and variable axes. BrandAI uses 16 px minimum body, 14 px metadata, a restrained weight set, and script-specific line height.
- Sources: https://github.com/google/fonts/blob/main/ofl/heebo/DESCRIPTION.en_us.html and https://github.com/google/fonts/blob/main/ofl/notosanshebrew/METADATA.pb

## Logo geometry

- Clear space is geometric, not subjective. BrandAI defines `x` from mark height/cap height, keeps at least `1x` on every side, enforces a 6% social safe zone, minimum digital size, approved anchors, and explicit distortion/recolor/effect prohibitions.
- Published institutional guidelines consistently treat clear space as an exclusion zone and prohibit typography/graphics entering it. Example: https://www.copyright.com/rightfind-resource-center/wp-content/uploads/sites/14/2021/06/brand-guidelines.pdf

## Photography and feed cohesion

- Art direction must lock lighting, perspective, depth-of-field family, material realism, color grade, and copy-safe negative space—not merely append adjectives to a prompt.
- The University of Washington visual guidelines explicitly describe art direction, color correction/cropping, and composing imagery with clear space for copy/graphics. Source: https://www.washington.edu/externalaffairs/uwmarketing/toolkits/docs/bg/UniversityBrandGuidelinesComplete.pdf
- BrandAI reuses grid, margins, type scale, logo anchor, and grading while rotating content formats. Thumbnail recognition is checked at 160 px; identity remains stable while individual posts vary.

## BiRefNet decision

- Official code license: MIT: https://github.com/ZhengPeng7/BiRefNet/blob/main/LICENSE
- Official Hugging Face model card labels the checkpoint MIT: https://huggingface.co/ZhengPeng7/BiRefNet
- Upstream reports standard 1024×1024 inference at approximately 4.8 GB FP32 or 3.5 GB FP16/BF16, so it fits the local 16 GB GPU when scheduled separately from FLUX: https://github.com/ZhengPeng7/BiRefNet#model-zoo
- The maintainer states code and weights are MIT, but later issues acknowledge that some training datasets have restrictive terms and a clean commercially licensed training configuration is not yet available. See https://github.com/ZhengPeng7/BiRefNet/issues/306 and https://github.com/ZhengPeng7/BiRefNet/issues/331
- Decision: implement the adapter and product workflow, but leave checkpoint execution disabled until the business accepts the residual dataset-provenance risk or a commercially cleared checkpoint is released.
