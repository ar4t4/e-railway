'use strict';

// Labels can be friendly; saved values must remain the railway's exact names.
const stationLabel = name => name.replaceAll('_', ' ');
const stationSearchKey = name => stationLabel(name).toLocaleLowerCase().replace(/\s+/g, ' ').trim();
let stationCatalogue, stationLoading;
async function loadStations() {
  if (stationCatalogue) return stationCatalogue;
  if (!stationLoading) stationLoading = fetch('/assets/stations.json').then(async response => {
    if (!response.ok) throw new Error('Could not load stations. Reopen this form to retry.');
    const data = await response.json();
    if (!Array.isArray(data.stations) || !data.stations.length) throw new Error('Station list is unavailable.');
    stationCatalogue = data.stations;
    return stationCatalogue;
  }).catch(error => { stationLoading = null; throw error; });
  return stationLoading;
}

class StationPicker {
  constructor(root, kind) {
    this.root = root;
    this.kind = kind;
    this.values = [];
    this.active = -1;
    this.options = [];
    this.catalogue = [];
    this.input = root.querySelector('input');
    this.list = root.querySelector('[role=listbox]');
    this.chips = root.querySelector('.station-chips');
    this.status = root.querySelector('[role=status]');
    this.input.addEventListener('focus', () => this.show());
    this.input.addEventListener('click', () => this.show());
    this.input.addEventListener('input', () => { this.input.setCustomValidity(''); this.show(); });
    this.input.addEventListener('keydown', event => this.keydown(event));
    this.list.addEventListener('mousedown', event => event.preventDefault());
    this.list.addEventListener('click', event => {
      const option = event.target.closest('[data-station-index]');
      if (option) this.add(this.options[Number(option.dataset.stationIndex)].name);
    });
    this.chips.addEventListener('click', event => {
      const remove = event.target.closest('[data-remove-station]');
      if (!remove) return;
      this.values.splice(Number(remove.dataset.removeStation), 1);
      this.renderChips();
      this.input.setCustomValidity('');
      this.input.focus();
      this.show();
    });
    root.addEventListener('focusout', () => setTimeout(() => {
      if (!root.contains(document.activeElement)) this.close();
    }, 0));
    document.addEventListener('click', event => { if (!root.contains(event.target)) this.close(); });
  }
  async reset(values) {
    this.values = [...values];
    this.input.value = '';
    this.input.setCustomValidity('');
    this.close();
    this.renderChips();
    this.status.textContent = 'Loading stations…';
    try {
      this.catalogue = (await loadStations()).filter(station => this.kind !== 'origins' || station.departure);
      this.status.textContent = 'Select up to 10, in your preferred order.';
      if (document.activeElement === this.input) this.show();
    } catch (error) { this.status.textContent = error.message; }
  }
  renderChips() {
    this.chips.replaceChildren();
    this.values.forEach((name, index) => {
      const chip = document.createElement('span');
      chip.className = 'station-chip';
      const label = document.createElement('span');
      label.textContent = `${index + 1}. ${stationLabel(name)}`;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.dataset.removeStation = index;
      remove.setAttribute('aria-label', `Remove ${stationLabel(name)}`);
      remove.textContent = '×';
      chip.append(label, remove);
      this.chips.append(chip);
    });
  }
  show() {
    const query = stationSearchKey(this.input.value);
    this.options = this.catalogue.filter(station => !this.values.includes(station.name) && stationSearchKey(station.name).includes(query));
    this.active = -1;
    this.input.removeAttribute('aria-activedescendant');
    this.list.replaceChildren();
    if (this.values.length >= 10 || !this.options.length) {
      const message = document.createElement('div');
      message.className = 'station-empty';
      message.textContent = this.values.length >= 10 ? '10 stations selected. Remove one to add another.' : this.catalogue.length ? 'No matching stations. Try a shorter name.' : 'Loading stations…';
      this.list.append(message);
    } else {
      this.options.forEach((station, index) => {
        const option = document.createElement('div');
        option.id = `${this.kind}-option-${index}`;
        option.className = 'station-option';
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', 'false');
        option.dataset.stationIndex = index;
        option.textContent = stationLabel(station.name);
        this.list.append(option);
      });
    }
    this.list.hidden = false;
    this.input.setAttribute('aria-expanded', 'true');
  }
  close() {
    this.list.hidden = true;
    this.input.setAttribute('aria-expanded', 'false');
    this.input.removeAttribute('aria-activedescendant');
    this.active = -1;
  }
  add(name) {
    if (!name || this.values.includes(name) || this.values.length >= 10) return;
    this.values.push(name);
    this.input.value = '';
    this.input.setCustomValidity('');
    this.renderChips();
    this.status.textContent = `${stationLabel(name)} added. ${this.values.length} of 10 selected.`;
    this.input.focus();
    this.show();
  }
  keydown(event) {
    if (event.key === 'Escape' && !this.list.hidden) {
      event.preventDefault(); event.stopPropagation(); this.close(); return;
    }
    if (event.key === 'Tab') { this.close(); return; }
    if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault();
      if (this.list.hidden) this.show();
      if (!this.options.length || this.values.length >= 10) return;
      this.active = this.active < 0 ? (event.key === 'ArrowDown' ? 0 : this.options.length - 1)
        : (this.active + (event.key === 'ArrowDown' ? 1 : -1) + this.options.length) % this.options.length;
      [...this.list.children].forEach((option, index) => option.setAttribute('aria-selected', String(index === this.active)));
      const option = this.list.children[this.active];
      this.input.setAttribute('aria-activedescendant', option.id);
      option.scrollIntoView({block: 'nearest'});
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      if (this.list.hidden) this.show();
      else if (this.active >= 0) this.add(this.options[this.active]?.name);
      else if (this.options.length === 1) this.add(this.options[0].name);
    }
  }
  validate() {
    this.input.setCustomValidity(this.values.length ? '' : 'Select at least one station from the list.');
    if (!this.values.length) { this.input.reportValidity(); return false; }
    if (this.input.value.trim()) {
      this.input.setCustomValidity('Select a matching station from the list, or clear your search.');
      this.input.reportValidity(); return false;
    }
    return true;
  }
}

const stationPickers = Object.fromEntries(['origins', 'destinations'].map(kind => [kind,
  new StationPicker(document.querySelector(`[data-station-picker="${kind}"]`), kind)]));
