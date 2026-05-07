import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("rs-master-detail")
export class RsMasterDetail extends LitElement {
  @property({ type: String }) public masterWidth = "260px";
  @property({ type: Number }) public breakpoint = 720;

  static styles = css`
    :host {
      display: block;
      container-type: inline-size;
    }

    .wrap {
      display: grid;
      grid-template-columns: var(--rs-master-width, 260px) minmax(0, 1fr);
      gap: 20px;
      align-items: start;
    }

    @container (max-width: 720px) {
      .wrap {
        grid-template-columns: minmax(0, 1fr);
      }
    }

    /* Fallback for browsers without container queries */
    @media (max-width: 720px) {
      .wrap {
        grid-template-columns: minmax(0, 1fr);
      }
    }

    ::slotted([slot="master"]) {
      min-width: 0;
    }

    ::slotted([slot="detail"]) {
      min-width: 0;
    }
  `;

  render() {
    return html`
      <div class="wrap" style="--rs-master-width: ${this.masterWidth};">
        <div class="master"><slot name="master"></slot></div>
        <div class="detail"><slot name="detail"></slot></div>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "rs-master-detail": RsMasterDetail;
  }
}
