"""Depth-first board solver for fitting drive shapes into role blueprints."""

import copy
from typing import List
from src.models.equipment import DriveShape

class DFSPuzzleSolver:
    def __init__(self, shapes_db: dict[str, DriveShape]):
        self.shapes_db = shapes_db

    def can_place(self, board: List[List[int]], piece_matrix: List[List[int]], start_r: int, start_c: int) -> bool:
        p_rows = len(piece_matrix)
        p_cols = len(piece_matrix[0])
        b_rows = len(board)
        b_cols = len(board[0])

        if start_r < 0 or start_c < 0 or start_r + p_rows > b_rows or start_c + p_cols > b_cols:
            return False

        for r in range(p_rows):
            for c in range(p_cols):
                if piece_matrix[r][c] == 1:
                    if board[start_r + r][start_c + c] != 0:
                        return False
        return True

    def place_piece(self, board: List[List[int]], piece_matrix: List[List[int]], start_r: int, start_c: int,
                    piece_id: str):
        for r in range(len(piece_matrix)):
            for c in range(len(piece_matrix[0])):
                if piece_matrix[r][c] == 1:
                    board[start_r + r][start_c + c] = piece_id

    def remove_piece(self, board: List[List[int]], piece_matrix: List[List[int]], start_r: int, start_c: int):
        for r in range(len(piece_matrix)):
            for c in range(len(piece_matrix[0])):
                if piece_matrix[r][c] == 1:
                    board[start_r + r][start_c + c] = 0

    def solve(self, board: List[List[int]], pieces_to_place: List[str], current_results: List[List[List[str]]], max_solutions: int = 0):
        if max_solutions > 0 and len(current_results) >= max_solutions:
            return

        if not pieces_to_place:
            current_results.append(copy.deepcopy(board))
            return

        b_rows, b_cols = len(board), len(board[0])
        target_r, target_c = -1, -1
        for r in range(b_rows):
            for c in range(b_cols):
                if board[r][c] == 0:
                    target_r, target_c = r, c
                    break
            if target_r != -1:
                break

        if target_r == -1:
            return

        unique_pieces = set(pieces_to_place)

        for piece_id in unique_pieces:
            piece_matrix = self.shapes_db[piece_id].matrix

            p_first_r, p_first_c = -1, -1
            for r in range(len(piece_matrix)):
                for c in range(len(piece_matrix[0])):
                    if piece_matrix[r][c] == 1:
                        p_first_r, p_first_c = r, c
                        break
                if p_first_r != -1:
                    break

            start_r = target_r - p_first_r
            start_c = target_c - p_first_c

            if self.can_place(board, piece_matrix, start_r, start_c):
                self.place_piece(board, piece_matrix, start_r, start_c, piece_id)
                next_pieces = list(pieces_to_place)
                next_pieces.remove(piece_id)
                self.solve(board, next_pieces, current_results, max_solutions)
                self.remove_piece(board, piece_matrix, start_r, start_c)
                if max_solutions > 0 and len(current_results) >= max_solutions:
                    return
