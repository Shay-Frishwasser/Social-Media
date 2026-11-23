# Figma import guide

Use the [editable BrandAI Design board](https://www.figma.com/design/iq1z0oRuSRGfSliYzu85cI?node-id=7-20) and review the [UX specification](../../docs/UX_HANDOFF_SPEC.md) alongside it. The English frames are arranged right to left so the designer can evaluate the proposed Hebrew workspace geometry before localizing the labels. Brand names, evidence and job states in the wireframes are illustrative.

## Place the reference board

1. Open a **Figma Design** file and add a page named **Reference board**.
2. Drag [wireframes-overview.svg](wireframes-overview.svg) from your filesystem directly onto the canvas. Zoom to selection, then zoom into the screen you need. Frames 01–04 run right to left on the first row and 05–08 run right to left on the second row. The eight related dialogs are grouped below. [Figma guidance for SVG on canvas](https://help.figma.com/hc/en-us/articles/360040030374-Copy-assets-between-design-tools)
3. Lock the imported board. The SVG is a single reference canvas with named vector groups and outlined English labels. Use the native Figma frames for editable text and component instances. Replace labels using the Hebrew microcopy in the spec once the base layouts are in review.

## Import the tokens

1. Open **Tokens Studio for Figma**. In **Settings → Token Format**, select **W3C DTCG** and create a local token set named **BrandAI**. [Tokens Studio token format](https://docs.tokens.studio/manage-settings/token-format)
2. Open that set in the **JSON** view, paste the contents of [design-tokens.json](design-tokens.json), and apply the changes. Confirm that `color.workspace.action`, `type.hebrew.body`, and `layout.rtl.directionSign` appear. Use the DTCG 2025.10 values as the source of truth. [Tokens Studio token value views](https://docs.tokens.studio/manage-tokens/token-values), [Design Tokens Format Module](https://www.designtokens.org/tr/2025.10/format/)
3. If an installed Tokens Studio release expects older DTCG value shapes, make an import-only copy. Convert each sRGB color object to its `hex` string, each dimension object to a unit string such as `16px`, and a composite typography `lineHeight` multiplier to a percentage such as `155%`. Retain `$type`, `$value` and aliases. Keep the committed file in DTCG 2025.10 format.
4. Bind workspace surfaces and interaction states separately from the example campaign color roles. Use the 60/30/10 ratios as relative color usage guidance in each brand’s content previews. Treat RTL flags as layout documentation and bind them to logical start/end in application code. Figma numeric variables alone do not mirror a layout.
5. Confirm that Heebo and Inter are available before editing Hebrew and English specimens. Reserve the editorial serif for high-level handoff titles. Recheck the 390 px mobile layout with long Hebrew labels, LTR URLs and numeric values.

The native file uses the board’s charcoal, lime and mint appearance as low-fidelity direction. The spec identifies backend dependencies such as pre-approval visual previews and direct GPU telemetry, so review frames can show the proposed interaction without promising an unavailable API.
