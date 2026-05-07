import { LitElement, html, css, nothing } from "lit";
import { customElement, property } from "lit/decorators.js";
import "./rs-toggle-row";

@customElement("rs-toggle-card")
export class RsToggleCard extends LitElement {
  @property({ type: String }) public icon = "";
  @property({ type: String }) public label = "";
  @property({ type: String }) public hint = "";
  @property({ type: Boolean }) public checked = false;
  @property({ type: Boolean }) public disabled = false;

  static styles = css`
    :host {
      display: block;
    }

    ha-card {
      padding: 16px 20px;
      min-width: 0;
    }

    .row {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .icon {
      --mdc-icon-size: 18px;
      color: var(--secondary-text-color);
      opacity: 0.7;
      flex-shrink: 0;
    }

    rs-toggle-row {
      flex: 1;
      min-width: 0;
    }
  `;

  render() {
    return html`
      <ha-card>
        <div class="row">
          ${this.icon ? html`<ha-icon class="icon" icon=${this.icon}></ha-icon>` : nothing}
          <rs-toggle-row
            .label=${this.label}
            .hint=${this.hint}
            .checked=${this.checked}
            .disabled=${this.disabled}
            @toggle-changed=${this._onToggle}
          ></rs-toggle-row>
        </div>
      </ha-card>
    `;
  }

  private _onToggle(e: CustomEvent<boolean>) {
    e.stopPropagation();
    this.dispatchEvent(
      new CustomEvent("toggle-changed", {
        detail: e.detail,
        bubbles: true,
        composed: true,
      }),
    );
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "rs-toggle-card": RsToggleCard;
  }
}
