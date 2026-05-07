import { LitElement, html, css, nothing } from "lit";
import { customElement, property } from "lit/decorators.js";
import type { HomeAssistant } from "../types";
import { localize } from "../utils/localize";
import "./shared/rs-toggle-row";

@customElement("rs-presence-section")
export class RsPresenceSection extends LitElement {
  @property({ attribute: false }) public hass!: HomeAssistant;
  @property({ type: Boolean }) public presenceEnabled = false;
  @property({ attribute: false }) public presencePersons: string[] = [];
  @property({ attribute: false }) public selectedPresencePersons: string[] = [];
  @property({ type: Boolean }) public ignorePresence = false;
  @property({ type: Boolean }) public editing = false;
  @property() public language = "en";

  static styles = css`
    :host {
      display: block;
    }

    .presence-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
      gap: 8px;
      margin-top: 12px;
    }

    .presence-card {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px 8px 6px;
      border: 1px solid var(--divider-color);
      border-radius: 10px;
      background: var(--card-background-color);
      cursor: pointer;
      transition:
        border-color 0.15s ease,
        background 0.15s ease;
      user-select: none;
    }

    .presence-card:hover {
      background: rgba(255, 255, 255, 0.03);
    }

    .presence-card.active {
      border-color: rgba(3, 169, 244, 0.4);
      background: rgba(3, 169, 244, 0.08);
    }

    .presence-card ha-checkbox {
      flex-shrink: 0;
    }

    .person-icon {
      --mdc-icon-size: 18px;
      color: var(--secondary-text-color);
      flex-shrink: 0;
    }

    .presence-card.active .person-icon {
      color: var(--primary-color);
    }

    .person-name {
      flex: 1;
      font-size: 14px;
      font-weight: 450;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .presence-list {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }

    .presence-row {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 14px;
      border-radius: 8px;
      transition: background 0.3s;
    }

    .presence-row.home {
      background: rgba(76, 175, 80, 0.1);
    }

    .presence-row.away {
      background: rgba(0, 0, 0, 0.04);
    }

    .presence-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex-shrink: 0;
    }

    .presence-row.home .presence-dot {
      background: #4caf50;
      box-shadow: 0 0 6px rgba(76, 175, 80, 0.5);
    }

    .presence-row.away .presence-dot {
      background: var(--disabled-text-color, #bdbdbd);
    }

    .presence-name {
      flex: 1;
      font-size: 14px;
      font-weight: 500;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .presence-row.home .presence-name {
      color: var(--primary-text-color);
    }

    .presence-row.away .presence-name {
      color: var(--secondary-text-color);
    }

    .presence-state {
      font-size: 12px;
      white-space: nowrap;
    }

    .presence-row.home .presence-state {
      color: #2e7d32;
    }

    .presence-row.away .presence-state {
      color: var(--secondary-text-color);
    }

    .section-divider {
      border-top: 1px solid var(--divider-color, #e0e0e0);
      margin: 8px 0;
    }

    .field-hint {
      color: var(--secondary-text-color);
      font-size: 12px;
    }
  `;

  render() {
    if (!this.presenceEnabled || this.presencePersons.length === 0) return nothing;

    if (!this.editing) {
      if (this.ignorePresence) {
        return html`<span class="field-hint"
          >${localize("presence.tile_ignored", this.language)}</span
        >`;
      }
      return this._renderViewMode();
    }

    return html`
      <rs-toggle-row
        .label=${localize("presence.ignore_toggle", this.language)}
        .checked=${this.ignorePresence}
        @toggle-changed=${this._onIgnoreToggle}
      ></rs-toggle-row>
      ${!this.ignorePresence
        ? html`<div class="section-divider"></div>
            ${this._renderEditMode()}`
        : nothing}
    `;
  }

  private _renderEditMode() {
    return html`
      <div class="presence-grid">
        ${this.presencePersons.map((pid) => {
          const active = this.selectedPresencePersons.includes(pid);
          const name =
            this.hass.states[pid]?.attributes?.friendly_name ?? pid.split(".").slice(1).join(".");
          return html`
            <div
              class="presence-card ${active ? "active" : ""}"
              role="checkbox"
              tabindex="0"
              aria-checked=${active}
              @click=${() => this._onTogglePerson(pid, active)}
              @keydown=${(e: KeyboardEvent) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  this._onTogglePerson(pid, active);
                }
              }}
            >
              <ha-checkbox
                .checked=${active}
                tabindex="-1"
                @click=${(e: Event) => e.stopPropagation()}
                @change=${() => this._onTogglePerson(pid, active)}
              ></ha-checkbox>
              <ha-icon class="person-icon" icon="mdi:account"></ha-icon>
              <span class="person-name">${name}</span>
            </div>
          `;
        })}
      </div>
    `;
  }

  private _renderViewMode() {
    if (this.selectedPresencePersons.length === 0) {
      return html`<span class="field-hint"
        >${localize("presence.room_none_assigned", this.language)}</span
      >`;
    }
    return html`
      <div class="presence-list">
        ${this.selectedPresencePersons.map((pid) => {
          const name =
            this.hass.states[pid]?.attributes?.friendly_name ?? pid.split(".").slice(1).join(".");
          const st = this.hass.states[pid]?.state;
          const isHome =
            pid.startsWith("person.") || pid.startsWith("device_tracker.")
              ? st === "home"
              : st === "on";
          return html`
            <div class="presence-row ${isHome ? "home" : "away"}">
              <span class="presence-dot"></span>
              <span class="presence-name">${name}</span>
              <span class="presence-state"
                >${isHome
                  ? localize("presence.state_home", this.language)
                  : localize("presence.state_away", this.language)}</span
              >
            </div>
          `;
        })}
      </div>
    `;
  }

  private _onIgnoreToggle(e: CustomEvent<boolean>) {
    this.dispatchEvent(
      new CustomEvent("ignore-presence-changed", {
        detail: e.detail,
        bubbles: true,
        composed: true,
      }),
    );
  }

  private _onTogglePerson(pid: string, currentlyActive: boolean) {
    let next: string[];
    if (currentlyActive) {
      next = this.selectedPresencePersons.filter((p) => p !== pid);
    } else {
      next = [...this.selectedPresencePersons, pid];
    }
    this.dispatchEvent(
      new CustomEvent("presence-persons-changed", {
        detail: next,
        bubbles: true,
        composed: true,
      }),
    );
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "rs-presence-section": RsPresenceSection;
  }
}
