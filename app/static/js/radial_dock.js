/**
 * Radial Action Dock: Spring-animated 4-action wheel
 * Provides context-sensitive actions: Move, Attack, Give AP, Upgrade Range
 */
class RadialActionDock {
  constructor(dockContainerId, onActionSelect) {
    this.container = document.getElementById(dockContainerId);
    this.onActionSelect = onActionSelect;
    this.isOpen = false;
    this.currentTarget = null;

    this.renderButtons();
  }

  renderButtons() {
    const moveText = window.I18N ? I18N.t('radial_move') : 'MOVE (1 AP)';
    const attackText = window.I18N ? I18N.t('radial_attack') : 'ATTACK (1 AP)';
    const giveText = window.I18N ? I18N.t('radial_give') : 'GIVE AP (1 AP)';
    const upgradeText = window.I18N ? I18N.t('radial_upgrade', { cost: 2 }) : 'UPGRADE RANGE';

    this.container.innerHTML = `
      <button class="radial-button move" id="radial-btn-move">${moveText}</button>
      <button class="radial-button attack" id="radial-btn-attack">${attackText}</button>
      <button class="radial-button give" id="radial-btn-give">${giveText}</button>
      <button class="radial-button upgrade" id="radial-btn-upgrade">${upgradeText}</button>
    `;

    // Position buttons in cardinal radial directions around center
    const radius = 64;
    const btnMove = document.getElementById('radial-btn-move');
    const btnAttack = document.getElementById('radial-btn-attack');
    const btnGive = document.getElementById('radial-btn-give');
    const btnUpgrade = document.getElementById('radial-btn-upgrade');

    // North: Move
    btnMove.style.top = `-${radius + 19}px`;
    btnMove.style.left = `-50px`;

    // East: Attack
    btnAttack.style.top = `-19px`;
    btnAttack.style.left = `${radius - 15}px`;

    // South: Give AP
    btnGive.style.top = `${radius - 19}px`;
    btnGive.style.left = `-50px`;

    // West: Upgrade Range
    btnUpgrade.style.top = `-19px`;
    btnUpgrade.style.left = `-${radius + 85}px`;

    btnMove.addEventListener('click', (e) => { e.stopPropagation(); this.triggerAction('MOVE'); });
    btnAttack.addEventListener('click', (e) => { e.stopPropagation(); this.triggerAction('ATTACK'); });
    btnGive.addEventListener('click', (e) => { e.stopPropagation(); this.triggerAction('GIVE_AP'); });
    btnUpgrade.addEventListener('click', (e) => { e.stopPropagation(); this.triggerAction('UPGRADE_RANGE'); });

    document.addEventListener('click', (e) => {
      if (this.isOpen && !this.container.contains(e.target)) {
        this.close();
      }
    });
  }

  openAt(clientX, clientY, targetContext) {
    this.currentTarget = targetContext;
    const { tileX, tileY, player, selfPlayer } = targetContext;

    const btnMove = document.getElementById('radial-btn-move');
    const btnAttack = document.getElementById('radial-btn-attack');
    const btnGive = document.getElementById('radial-btn-give');
    const btnUpgrade = document.getElementById('radial-btn-upgrade');

    // Context-sensitive availability
    if (targetContext.mode === 'UPGRADE_ONLY') {
      btnMove.style.display = 'none';
      btnAttack.style.display = 'none';
      btnGive.style.display = 'none';
      btnUpgrade.style.display = 'flex';
    } else if (targetContext.mode === 'COMBAT_ONLY') {
      btnMove.style.display = 'none';
      btnUpgrade.style.display = 'none';
      btnAttack.style.display = 'flex';
      btnGive.style.display = 'flex';
    } else {
      const isSelf = player && (player.is_self || (selfPlayer && player.id === selfPlayer.id));
      const isEmpty = !player;
      const isAdjacent = selfPlayer && (Math.abs(tileX - selfPlayer.x) + Math.abs(tileY - selfPlayer.y) === 1);

      btnMove.style.display = (isEmpty && isAdjacent) ? 'flex' : 'none';
      btnUpgrade.style.display = isSelf ? 'flex' : 'none';
      btnAttack.style.display = (!isSelf && player && player.is_alive) ? 'flex' : 'none';
      btnGive.style.display = (!isSelf && player && player.is_alive) ? 'flex' : 'none';
    }

    // Position container
    const boardContainer = document.querySelector('.board-container');
    const bRect = boardContainer ? boardContainer.getBoundingClientRect() : { left: 0, top: 0 };
    const x = clientX - bRect.left;
    const y = clientY - bRect.top;

    this.container.style.left = `${x}px`;
    this.container.style.top = `${y}px`;
    this.container.classList.add('open');
    
    // Defer isOpen flag so current bubbling click event doesn't immediately close it
    setTimeout(() => {
      this.isOpen = true;
    }, 60);
  }

  close() {
    this.container.classList.remove('open');
    this.isOpen = false;
    this.currentTarget = null;
  }

  triggerAction(actionType) {
    if (this.onActionSelect && this.currentTarget) {
      this.onActionSelect(actionType, this.currentTarget);
    }
    this.close();
  }
}
