/**
 * Match Replay Timeline Scrubber & Diff Visualizer
 */
class ReplayController {
  constructor(sliderId, labelId, onSeqChange) {
    this.slider = document.getElementById(sliderId);
    this.label = document.getElementById(labelId);
    this.onSeqChange = onSeqChange;
    this.maxSeq = 0;

    if (this.slider) {
      this.slider.addEventListener('input', (e) => {
        const val = parseInt(e.target.value, 10);
        this.updateLabel(val);
        if (this.onSeqChange) {
          this.onSeqChange(val, val === this.maxSeq);
        }
      });
    }
  }

  setMaxSequence(maxSeq) {
    this.maxSeq = Math.max(0, maxSeq || 0);
    if (this.slider) {
      if (this.maxSeq <= 1) {
        this.slider.min = 1;
        this.slider.max = 1;
        this.slider.value = 1;
        this.slider.disabled = true;
      } else {
        this.slider.disabled = false;
        this.slider.min = 1;
        this.slider.max = this.maxSeq;
        this.slider.value = this.maxSeq;
      }
      this.updateLabel(this.maxSeq);
    }
  }

  updateLabel(val) {
    if (this.label) {
      if (val >= this.maxSeq || this.maxSeq <= 1) {
        this.label.textContent = window.I18N ? I18N.t('replay_live') : "LIVE STATE";
        this.label.style.color = "var(--primary-cyan)";
      } else {
        this.label.textContent = window.I18N ? I18N.t('replay_event_seq', { seq: val }) : `Event #${val}`;
        this.label.style.color = "var(--energy-amber)";
      }
    }
  }
}
