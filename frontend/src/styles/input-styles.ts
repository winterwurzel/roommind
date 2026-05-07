import { css } from "lit";

/**
 * Rounded-corner overrides for HA's MDC-based inputs.
 *
 * `--mdc-shape-small` rounds the top corners of filled text fields (the
 * built-in MDC styles hardcode the bottom to 0). Setting `border-radius`
 * + `overflow: hidden` on the host element clips the bottom too so the
 * inputs match the rest of the rounded card design.
 */
export const inputStyles = css`
  ha-textfield,
  ha-select,
  ha-entity-picker,
  ha-combo-box {
    --mdc-shape-small: 8px;
    --mdc-shape-medium: 8px;
    --md-filled-text-field-container-shape: 8px;
    --md-outlined-text-field-container-shape: 8px;
    display: block;
    border-radius: 8px;
    overflow: hidden;
    isolation: isolate;
    /* clip-path is more reliable than overflow:hidden for shape clipping
       on the bottom corners of MDC filled inputs. */
    clip-path: inset(0 round 8px);
  }

  /* ha-entity-picker wraps an inner ha-combo-box that doesn't always
     reach the host's bottom edge. Use a tighter clip so the visible
     input's bottom matches the inner's top radius. */
  ha-entity-picker {
    clip-path: inset(0 round 8px 8px 4px 4px);
  }
`;
