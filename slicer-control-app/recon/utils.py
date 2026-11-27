# utils.py
import numpy as np

def build_poly_design_matrix(x, y, degree):
    """Highly optimized vectorized polynomial matrix generation using NumPy broadcasting."""
    # Pre-calculate powers using 2D broadcasted arrays (vectorized)
    x_powers = np.power.outer(x, np.arange(degree + 1))
    y_powers = np.power.outer(y, np.arange(degree + 1))
    
    # Pre-allocate memory block for the final matrix
    num_cols = (degree + 1) * (degree + 2) // 2
    A = np.empty((len(x), num_cols), dtype=np.float64)
    
    col_idx = 0
    for i in range(degree + 1):
        for j in range(degree + 1 - i):
            A[:, col_idx] = x_powers[:, i] * y_powers[:, j]
            col_idx += 1
    return A

def build_grid_triangles(grid_res):
    """Generates standard indices to stitch a grid of resolution grid_res into triangles."""
    triangles = []
    for i in range(grid_res - 1):
        for j in range(grid_res - 1):
            v0 = i * grid_res + j
            v1 = v0 + 1
            v2 = v0 + grid_res
            v3 = v2 + 1
            triangles.append([v0, v1, v3])
            triangles.append([v0, v3, v2])
    return np.array(triangles)