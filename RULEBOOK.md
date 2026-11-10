# ALLIANCE: THE BOARD GAME RULEBOOK
*(A Tabletop Strategy Game of Spatial Positioning, Resource Secrecy, and Diplomatic Accord)*

---

## 1. Introduction & Overview

**ALLIANCE** is an asynchronous social-strategy tabletop game played on an open coordinate grid. Players navigate the board, manage scarce daily Action Points (AP), forge trust-based alliances, transfer energy across interaction lines, and balance personal victory against collective peace.

Every match operates on clear, deterministic rules: spatial movement and range are mathematically defined, while game tension arises entirely from incomplete knowledge, hidden reserves, and the delicate nature of human trust.

---

## 2. The Three Paths to Victory

A game of Alliance concludes in exactly one of three ways:

1. **Sole Survivor (Last Player Standing):**  
   Play proceeds until only one active player remains on the board. That player is declared the sole victor.

2. **The Peace Accord (Diplomatic Conference):**  
   When players convene a formal diplomatic conference, all active participants vote on whether to conclude the game together.
   * **Unanimous Accord (All Agree):** If every active player votes in favor of peace, the game ends in a shared **Collective Victory** for all survivors.
   * **Dissent & Betrayal:** If one or more players reject the accord (or abstain), the conference collapses. Depending on the rules chosen for the match, dissent triggers one of three outcomes:
     - **Betrayal Victory:** The dissenter (or faction of dissenters) exploits the goodwill of other players to claim an immediate victory.
     - **Bonus AP & Resume Play:** Dissenters receive bonus Action Points, and normal play immediately resumes.
     - **Resume Play:** The conference dissolves without bonuses, and play resumes immediately.

3. **Inactivity Timeout (Stagnation):**  
   If no movement or actions occur across the grid before the inactivity deadline expires, the match concludes in an automatic draw (or sudden death grid shrink, if configured).

---

## 3. Components & Board Setup

### 3.1 The Grid
* The board is a rectangular grid divided into coordinate squares.
* Horizontal columns are identified by letters and vertical rows by numbers.
* **Stacking Rule:** Exactly one player per square. A player cannot enter an occupied square, nor can they leap or pass through another player during movement.

### 3.2 Player Tokens
Each player on the board is represented by:
* **Username:** The player's unique name.
* **Emblem / Flag:** A national or custom banner (such as United Nations, Finland, Pride, Checkered Flag, or Peace Flag).
* **Avatar:** An optional custom portrait or flag fallback.

### 3.3 Initial Placement
At the start of a match, each registered player is placed onto a randomly selected, unoccupied square on the grid.

---

## 4. Hearts & The Action Economy

### 4.1 Hearts (Resilience)
* **Starting Hearts:** Players begin with **3 Hearts** (configurable from 1 to 10).
* **Taking Damage:** Each successful strike landed by an opponent removes 1 Heart.
* **Elimination:** When a player's Hearts reach 0, they are eliminated from active play and marked with an `[X]`. Eliminated players cannot move, strike, or transfer points.

### 4.2 Action Points (AP)
Action Points are the energy currency required to perform actions on the board.
* **Starting AP:** Players begin with **0 AP** (configurable from 0 to 10).
* **The Daily Drop:** Every 24 hours at the scheduled server drop time (default: **22:00:00**), every active player receives **+1 AP** (configurable).
* **Opening Grace Period:** Matches may feature a delay before the first AP distribution. This grants players time to study the board, establish communications, and negotiate initial agreements before active moves begin.
* **Banking AP:** Unspent Action Points accumulate indefinitely with no upper limit.
* **Elimination Forfeit:** When a player is eliminated, all their banked AP is removed from the game; resources cannot be looted from eliminated players.

---

## 5. Player Actions Reference

Active players may spend their banked Action Points at any time to execute the following actions:

| Action | Cost | Target | Rules & Limitations |
| :--- | :---: | :---: | :--- |
| **MOVE** | **1 AP** | Adjacent Square | Move 1 square orthogonally: North (▲), South (▼), East (►), or West (◄). Diagonal movement is prohibited. The target square must be unoccupied and within grid boundaries. |
| **STRIKE** | **1 AP** | Player in Range | Challenge another player within your interaction range. Removes **1 Heart** from the target. Intervening players do not block line of effect. |
| **GIFT / TRANSFER AP** | **1 AP** | Player in Range | Transfer 1 Action Point to a player within your interaction range. By default, transfers have zero fee: 1 AP is deducted from your balance and 1 AP is credited to the recipient. |
| **UPGRADE RANGE** | **2 AP** | Self | Permanently widens your interaction range by +1 tier. Upgrades stack indefinitely. |

### Command Batches & Instant Play
Players may issue orders in two convenient ways:
* **Instant Execution:** Actions are applied immediately as you click destinations, targets, or range upgrades.
* **Command Batching:** Actions can be planned into a sequence (up to the room's Burst Cap, default 3 AP) and executed together as a single atomic batch.

---

## 6. Interaction Range Geometry

All strikes and AP gifts require the target to be inside your **Interaction Range**.

Other players do **not** block line of effect: you may strike or transfer energy across intervening players, as long as the target square falls within your range perimeter.

### Mathematical Perimeter
For a player at square $P$ with Range Level $L$ (starting at Level 1):
* **Core Square:** A square of radius $R = L + 1$, spanning $(2L + 3) \times (2L + 3)$ squares centered on $P$.
* **Cardinal Spikes:** 4 orthogonal spikes extending 1 square further in each cardinal direction (North, South, East, West) at distance $R + 1 = L + 2$.

### Level 1 Range Perimeter (Starting Base)
At Level 1, the core square has radius 2 (a 5×5 box), with cardinal spikes at distance 3:

```
- - - x - - -
- x x x x x -
- x x x x x -
x x x P x x x
- x x x x x -
- x x x x x -
- - - x - - -
```
*Legend: `P` = Your Position, `x` = Targetable Square in Range, `-` = Out of Range.*

### Level 2 Range Perimeter (First Upgrade)
At Level 2, the core square expands to radius 3 (a 7×7 box), with cardinal spikes at distance 4:

```
- - - - x - - - -
- x x x x x x x -
- x x x x x x x -
- x x x x x x x -
x x x x P x x x x
- x x x x x x x -
- x x x x x x x -
- x x x x x x x -
- - - - x - - - -
```

### Level 3 and Beyond
Each subsequent upgrade widens the core box by +1 square in all directions and pushes the cardinal spikes 1 step further outward.

---

## 7. Fog of War & Concealed Action

Information on the board is divided into public and confidential layers:

* **Public Information:** Board boundaries, player coordinates, elimination announcements, and the room's audit log are visible to everyone.
* **Confidential Information:** Exact remaining Hearts, banked Action Points, and upgraded Range levels of other players remain secret.
* **Concealed Strikes (Unknown Attacker):**  
  When a player is struck but survives (a non-lethal hit), they receive a private notification that they took 1 damage and see their own remaining Hearts. However, **the attacker's identity is completely concealed**. The attacker learns that their strike connected, but does not see the defender's remaining Hearts.
* **Elimination Notice:**  
  Only when a player is eliminated (reduced to 0 Hearts) is the decisive hit announced: the eliminated player receives a private receipt revealing who eliminated them, and a public announcement is posted to the room ledger.

---

## 8. The Diplomatic Conference

The Diplomatic Conference provides an official forum to resolve the game through negotiation and mutual agreement rather than mutual elimination.

### 8.1 Convening the Conference
1. Active players call for a conference by casting an accord petition. Petitions may be toggled or withdrawn at any time before the threshold is reached.
2. When the petition tally reaches the configured **Conference Trigger Threshold** (default: 100% of active players), the conference formally opens.
3. Hosts may choose to mask petition counts prior to convening to keep diplomatic intentions private.

### 8.2 Deliberations, Voting & Resolution Timing
* When the conference convenes, a deliberation countdown timer begins (default: 120 seconds; configurable from 15 seconds to multiple hours).
* Each active player receives two ballot choices: **AGREE** (ratify the peace accord) or **REJECT** (refuse the accord).
* Players may freely discuss and coordinate during the open countdown.
* **Resolution Timing Rule (Conclusion Trigger):** Match hosts can specify whether the conference concludes as soon as every active player has cast a ballot, or remains open strictly until the countdown timer expires (allowing players to adjust ballots and negotiate right up to the final second).

### 8.3 The Treaty Ratification Rule (Silence is Not Consent)
**Abstentions count as Dissent.**  
If a player fails to cast a ballot before the countdown window expires, their silence is officially recorded as dissenting. A collective peace victory requires the active, conscious agreement of every living player.

### 8.4 Conference Outcomes
When the countdown ends, ballots are evaluated:

1. **Unanimous Accord (All Agree):**  
   Every active player agreed to peace. The game concludes immediately with all survivors sharing a **Collective Victory**.

2. **Single Dissenter (Exactly 1 Disagrees):**  
   Exactly one player voted against peace or abstained. The conference collapses, enacting the match's configured single-dissenter rule:
   * **Solo Betrayal Victory:** The lone holdout wins the game alone.
   * **Grant Bonus AP & Resume:** The lone holdout receives an AP bonus (e.g. +3 AP), and active play resumes.
   * **Resume Play:** Normal play resumes without bonuses.

3. **Multiple Dissenters (2 or More Disagree):**  
   Two or more players voted against peace or abstained. The conference collapses, enacting the match's configured multi-dissenter rule:
   * **Dissenters Share Betrayal Victory:** The dissenting players share a collective victory.
   * **Grant Bonus AP & Resume:** Each dissenting player receives bonus AP (e.g. +2 AP), and active play resumes.
   * **Resume Play:** Normal play resumes without bonuses.

### 8.5 Transparency Modes
The transparency mode determines what information is visible during deliberations:
* **Reveal Counts at Conclusion (Default):** Ballots remain private during voting; total counts for and against are published when the conference ends.
* **Show How Each Player Votes:** Every player's vote is displayed in real-time as it is cast.
* **Show Total Number Who Agreed:** The running tally of agreeing votes is visible, but individual choices remain private.
* **Keep Secret Throughout & Resolve Directly:** Ballots and counts remain private throughout, revealing only the final resolution.

### 8.6 Resolution Screen & Tactical Cooldown
When a conference collapses without unanimous accord, the board enters a brief 10-second resolution window. Tactical grid actions remain strictly blocked during this cooldown while all participants review the official outcome. In accordance with match transparency settings, vote counts or player ballots are displayed, and operatives granted bonus Action Points receive a confidential receipt. Unanimous peace accords skip the cooldown, transitioning immediately to the collective victory debriefing.

---

## 9. Strategic Considerations

1. **Range vs. Mobility:**  
   Spending 2 AP to upgrade your range gives you extended reach without moving, allowing you to influence more of the board. However, spending AP on range temporarily leaves you with less energy for movement and strikes compared to an active neighbor.

2. **The Hidden Reserve:**  
   Because Action Points are confidential, an opponent never knows whether you have 0 AP or enough for a multi-action sequence. The mere possibility of a rapid response is a powerful diplomatic deterrent.

3. **Concealed Strikes:**  
   Because non-lethal strikes do not reveal who made them, a player with sufficient range can strike between two rivals, creating uncertainty and shifting dynamics without exposing themselves.

4. **The Trust Dilemma:**  
   When betrayal victory is enabled, convening a conference tests every relationship. If all but one player vote for peace in good faith while one secretly dissents, that dissenter takes the victory. Alliances require genuine coordination and careful observation.
