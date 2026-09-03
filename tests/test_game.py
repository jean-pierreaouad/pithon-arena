import time
from types import SimpleNamespace

import pytest

from server import (
    GAME_SECONDS,
    INIT_HP,
    MAX_PIES,
    OBSTACLE_DMG,
    PIE_TABLE,
    PW_SPEED_TICKS,
    TICK,
    WALL_DMG,
    Game,
)


@pytest.fixture
def game_factory():
    def make_game():
        players = [
            SimpleNamespace(username="Ada", pdir=None),
            SimpleNamespace(username="Bob", pdir=None),
        ]
        game = Game(*players)
        game._last_pw = time.time()
        return game

    return make_game


def set_safe_pies(game):
    """Keep tick-based tests deterministic without changing spawn behavior."""
    game.pies = [
        {"pos": [x, 1], "type": "apple", "value": PIE_TABLE["apple"]}
        for x in range(1, MAX_PIES + 1)
    ]


def test_game_initializes_both_players_with_expected_state(game_factory):
    game = game_factory()

    assert game.time_left == float(GAME_SECONDS)
    assert len(game.pies) == MAX_PIES
    assert [snake["health"] for snake in game.snakes] == [INIT_HP, INIT_HP]
    assert [snake["alive"] for snake in game.snakes] == [True, True]
    assert [snake["shield"] for snake in game.snakes] == [False, False]
    assert [snake["sptick"] for snake in game.snakes] == [0, 0]
    assert [snake["dir"] for snake in game.snakes] == [[1, 0], [-1, 0]]


def test_wall_and_obstacle_collisions_deal_configured_damage(game_factory):
    wall_game = game_factory()
    wall_snake = wall_game.snakes[0]
    wall_snake["body"] = [[0, 4], [1, 4], [2, 4]]
    wall_snake["dir"] = [-1, 0]

    wall_game._step(0)

    assert wall_snake["health"] == INIT_HP - WALL_DMG
    assert wall_snake["body"] == [[0, 4], [1, 4], [2, 4]]

    obstacle_game = game_factory()
    obstacle_snake = obstacle_game.snakes[0]
    obstacle_snake["body"] = [[9, 5], [8, 5], [7, 5]]
    obstacle_snake["dir"] = [1, 0]

    obstacle_game._step(0)

    assert obstacle_snake["health"] == INIT_HP - OBSTACLE_DMG
    assert obstacle_snake["body"] == [[9, 5], [8, 5], [7, 5]]


def test_healing_and_damaging_pies_adjust_health_and_grow_snake(game_factory):
    healing_game = game_factory()
    healing_snake = healing_game.snakes[0]
    healing_snake["body"] = [[5, 5], [4, 5], [3, 5]]
    healing_snake["health"] = 80
    healing_game.pies = [
        {"pos": [6, 5], "type": "apple", "value": PIE_TABLE["apple"]}
    ]

    healing_game._step(0)

    assert healing_snake["health"] == 90
    assert len(healing_snake["body"]) == 4
    assert healing_game.pies == []

    damaging_game = game_factory()
    damaging_snake = damaging_game.snakes[0]
    damaging_snake["body"] = [[5, 5], [4, 5], [3, 5]]
    damaging_game.pies = [
        {"pos": [6, 5], "type": "rotten", "value": PIE_TABLE["rotten"]}
    ]

    damaging_game._step(0)

    assert damaging_snake["health"] == INIT_HP - 15
    assert len(damaging_snake["body"]) == 4
    assert damaging_game.pies == []


def test_shield_pickup_absorbs_only_the_next_collision(game_factory):
    game = game_factory()
    snake = game.snakes[0]
    snake["body"] = [[5, 5], [4, 5], [3, 5]]
    game.pies = []
    game.powerups = [
        {"pos": [6, 5], "type": "shield", "exp": time.time() + 60}
    ]

    game._step(0)

    assert snake["shield"] is True
    assert game.powerups == []

    snake["body"] = [[0, 4], [1, 4], [2, 4]]
    snake["dir"] = [-1, 0]
    game._step(0)

    assert snake["health"] == INIT_HP
    assert snake["shield"] is False

    game._step(0)

    assert snake["health"] == INIT_HP - WALL_DMG


def test_tick_rejects_reversal_but_accepts_perpendicular_turn(game_factory):
    game = game_factory()
    snake = game.snakes[0]
    snake["body"] = [[5, 5], [4, 5], [3, 5]]
    snake["dir"] = [1, 0]
    set_safe_pies(game)

    game.players[0].pdir = [-1, 0]
    assert game.tick() is True

    assert snake["dir"] == [1, 0]
    assert snake["body"][0] == [6, 5]
    assert game.players[0].pdir is None

    game.players[0].pdir = [0, -1]
    assert game.tick() is True

    assert snake["dir"] == [0, -1]
    assert snake["body"][0] == [6, 4]


def test_tick_ends_game_when_a_player_runs_out_of_health(game_factory):
    game = game_factory()
    snake = game.snakes[0]
    snake["body"] = [[0, 4], [1, 4], [2, 4]]
    snake["dir"] = [-1, 0]
    snake["health"] = WALL_DMG
    set_safe_pies(game)

    assert game.tick() is False

    assert snake["health"] == 0
    assert snake["alive"] is False
    assert game.winner == "Bob"
    assert game.reason == "Ada ran out of health"


def test_tick_ends_game_on_time_limit_using_remaining_health(game_factory):
    game = game_factory()
    game.time_left = TICK
    game.snakes[0]["health"] = 75
    game.snakes[1]["health"] = 60
    set_safe_pies(game)

    assert game.tick() is False

    assert game.winner == "Ada"
    assert game.reason == "Time limit reached"


def test_speed_pickup_causes_two_moves_for_configured_number_of_ticks(game_factory):
    game = game_factory()
    snake = game.snakes[0]
    snake["body"] = [[5, 5], [4, 5], [3, 5]]
    snake["dir"] = [1, 0]
    game.pies = []
    game.powerups = [
        {"pos": [6, 5], "type": "speed", "exp": time.time() + 60}
    ]

    game._step(0)

    assert snake["sptick"] == PW_SPEED_TICKS
    assert game.powerups == []

    set_safe_pies(game)
    assert game.tick() is True

    assert snake["body"][0] == [8, 5]
    assert snake["sptick"] == PW_SPEED_TICKS - 1
