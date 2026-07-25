/**
 * The caption style, as CSS.
 *
 * The preview has to look like the render or it is decoration rather than a
 * preview, so this mirrors what `daastaan_agent.video_edit` writes into the ASS
 * style block: the same colours, the same outline, and a box drawn in the outline
 * colour at the same opacity, because that is the `BackColour` libass uses when
 * `BorderStyle` is 3.
 *
 * Font stacks lead with the family the worker asks fontconfig for, so a developer
 * who happens to have DejaVu or Noto installed sees the real thing. Everyone else
 * falls through to the closest widely-installed metric match, which gets the size
 * and weight right even when the letterforms differ slightly.
 */

import type { CSSProperties } from 'react'
import type { AspectRatio, CaptionFont, CaptionStyle } from '../../types'

const FONT_STACKS: Record<CaptionFont, string> = {
  sans: "'DejaVu Sans', Verdana, Geneva, sans-serif",
  serif: "'DejaVu Serif', Georgia, 'Times New Roman', serif",
  mono: "'DejaVu Sans Mono', 'SF Mono', Menlo, Consolas, monospace",
  noto_sans: "'Noto Sans', 'Helvetica Neue', Arial, sans-serif",
  noto_serif: "'Noto Serif', Georgia, serif",
}

/** `#RRGGBB` plus an alpha to `rgba()`. The manifest's pattern guarantees the
 *  input shape, so this does not need to defend against anything else. */
function rgba(hex: string, alpha: number): string {
  const red = parseInt(hex.slice(1, 3), 16)
  const green = parseInt(hex.slice(3, 5), 16)
  const blue = parseInt(hex.slice(5, 7), 16)
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`
}

/**
 * `scale` converts render pixels to preview pixels.
 *
 * `size_pt` and `margin_px` are pixels at the delivery resolution, and the
 * preview is a fraction of that. Without scaling, a 44px caption chosen for a
 * 1280-wide frame would fill a 480-wide preview and look nothing like the export.
 */
export function captionCss(style: CaptionStyle, scale: number): CSSProperties {
  const size = Math.max(style.size_pt * scale, 7)
  const outline = style.outline_px * scale
  const shadow = style.shadow_px * scale

  return {
    fontFamily: FONT_STACKS[style.font],
    fontSize: `${size}px`,
    fontWeight: style.bold ? 700 : 400,
    fontStyle: style.italic ? 'italic' : 'normal',
    color: style.primary_color,
    lineHeight: 1.25,
    // `paint-order` puts the stroke behind the fill, which is how libass draws an
    // outline. Without it the stroke eats into the letterforms at wide widths.
    WebkitTextStrokeWidth: outline ? `${outline}px` : undefined,
    WebkitTextStrokeColor: outline ? style.outline_color : undefined,
    paintOrder: 'stroke fill',
    textShadow: shadow ? `0 ${shadow}px ${shadow * 1.5}px ${rgba('#000000', 0.7)}` : undefined,
    background: style.box ? rgba(style.outline_color, style.box_opacity) : 'transparent',
    padding: style.box ? `${0.18 * size}px ${0.42 * size}px` : 0,
    borderRadius: style.box ? `${Math.max(2, 0.12 * size)}px` : 0,
  }
}

/** Where the caption block sits inside the frame, matching the ASS alignment and
 *  `MarginV` the renderer uses. */
export function captionPlacement(style: CaptionStyle, scale: number): CSSProperties {
  const margin = style.margin_px * scale
  if (style.position === 'top') {
    return { top: `${margin}px`, alignItems: 'flex-start' }
  }
  if (style.position === 'middle') {
    return { top: 0, bottom: 0, alignItems: 'center' }
  }
  return { bottom: `${margin}px`, alignItems: 'flex-end' }
}

/** `16:9` to the `aspect-ratio` value CSS wants. */
export function aspectCss(aspect: AspectRatio): string {
  return aspect.replace(':', ' / ')
}

/** Decibels to a media element's 0..1 volume.
 *
 *  Clamped at 1, which means a positive gain cannot be previewed - the Web Audio
 *  graph needed to boost past unity is not worth building for a preview, and the
 *  renderer applies the real figure.
 */
export function gainToVolume(db: number): number {
  return Math.min(1, Math.max(0, 10 ** (db / 20)))
}
