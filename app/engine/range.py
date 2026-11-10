"""Range geometry calculation: core box + orthogonal cardinal spikes."""
from typing import Tuple, List, Set


def get_core_radius(range_level: int) -> int:
    """
    Return core radius R for level L (starting level 1).
    Level 1: R = 2 (5x5 square core)
    Level 2: R = 3 (7x7 square core)
    Level L: R = L + 1
    """
    return max(1, range_level + 1)


def is_in_range(from_pos: Tuple[int, int], to_pos: Tuple[int, int], range_level: int) -> bool:
    """
    Evaluate if to_pos is within interaction range of from_pos according to Section 4.2 & 12.6.
    Core Box: max(|x - x0|, |y - y0|) <= R
    Cardinal Spikes: distance R + 1 in orthogonal directions
    """
    x0, y0 = from_pos
    x, y = to_pos

    if x == x0 and y == y0:
        return False  # Target cannot be self tile for interaction targets

    r = get_core_radius(range_level)
    dx = abs(x - x0)
    dy = abs(y - y0)

    # Core square check
    if max(dx, dy) <= r:
        return True

    # Orthogonal cardinal spikes check at distance R + 1
    if (dx == r + 1 and dy == 0) or (dy == r + 1 and dx == 0):
        return True

    return False


def get_range_tiles(
    center_pos: Tuple[int, int],
    range_level: int,
    board_width: int,
    board_height: int,
    shrink_level: int = 0
) -> Set[Tuple[int, int]]:
    """
    Generate all valid board coordinates within range footprint, bounded by board dimensions
    and optional sudden death perimeter shrink.
    """
    x0, y0 = center_pos
    r = get_core_radius(range_level)
    tiles: Set[Tuple[int, int]] = set()

    min_x = 1 + shrink_level
    max_x = board_width - shrink_level
    min_y = 1 + shrink_level
    max_y = board_height - shrink_level

    # Core box tiles
    for x in range(x0 - r, x0 + r + 1):
        for y in range(y0 - r, y0 + r + 1):
            if min_x <= x <= max_x and min_y <= y <= max_y:
                tiles.add((x, y))

    # Cardinal spikes
    spikes = [
        (x0, y0 + r + 1),
        (x0, y0 - (r + 1)),
        (x0 + r + 1, y0),
        (x0 - (r + 1), y0)
    ]
    for sx, sy in spikes:
        if min_x <= sx <= max_x and min_y <= sy <= max_y:
            tiles.add((sx, sy))

    return tiles
