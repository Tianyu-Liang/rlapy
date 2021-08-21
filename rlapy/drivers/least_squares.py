"""
Routines for (approximately) solving over-determined least squares problems

    min{ || A x - b ||_2 + reg || x ||_2 : x in R^n }.
"""
#TODO: implement LSRN's solver for strongly under-determined problems.
import warnings
import scipy.linalg as la
import numpy as np
import rlapy.comps.deterministic as de


class OverLstsqSolver:
    """Solver for overdetermined ordinary least-squares."""

    def exec(self, A, b, tol, iter_lim, rng):
        """
        Return an approximate solution to
            min{ ||A x - b||_2 : x in R^n }.

        There is no requirement that an implementation is able to control
        the error of its returned solution. Some implementations will produce
        a solution by an iterative method. We can regard non-iterative
        implementations as iterative methods that take only one step.

        Parameters
        ----------
        A : Union[ndarray, spmatrix, LinearOperator]
            Tall data matrix for overdetermined ordinary least squares.

        b : ndarray
            Right-hand-side.

        tol : float
            This parameter is only relevant for implementations that involve
            some iterative method. Those implementations must have some kind
            of error metric (e.g., backward error in the normal equations)
            for a candidate solution. If the implementation's measurement of
            error falls below tol, then it returns the candidate solution.

            If an implementation does not use an iterative method and receives
            tol is not NaN, then a warning will be raised.

        iter_lim : int
            We require iter_lim > 0. Typically, iter_lim << A.shape[1].
            This parameter is only relevant for implementations that involve
            some kind of iterative method. Those implementations must terminate
            after iter_lim iterations.

            If an implementation does not use an iterative method and receives
            iter_lim > 1, then a warning will be raised.

        rng : Union[None, int, SeedSequence, BitGenerator, Generator]
            Determines the numpy Generator object that manages any and all
            randomness in this function call.

        Returns
        -------
        x_star : ndarray
            x_star.shape == (A.shape[1],). Approximate solution to the least
            squares problem under consideration.

        Notes
        -----
        Implementations are allowed to store problem-dependent metadata
        (such as the random sketch of A) after a call to self.exec(...).
        Any information other than the return value "x_star" must be recorded
        as metadata.
        """
        raise NotImplementedError()


def dim_checks(sampling_factor, n_rows, n_cols):
    assert n_rows >= n_cols
    d = int(sampling_factor * n_cols)
    if d > n_rows:
        msg = """
        The embedding dimension "d" should not be larger than the 
        number of rows of the data matrix. Here, an embedding dimension
        of d={d} has been requested for a matrix with only {n_rows} rows.
        We will proceed by setting d={n_rows}. This parameter choice will
        result in a very inefficient algorithm!
        """
        # ^ Python 3.6 parses that string to drop-in the symbols d and n_rows.
        warnings.warn(msg)
        d = n_rows
    assert d >= n_cols
    return d


class SAS1(OverLstsqSolver):
    """A sketch-and-solve approach to overdetermined ordinary least squares.

    When constructing objects from this class, users may specify the LAPACK
    driver to be used in solving sketched least squares problems.

    References
    ----------
    The sketch-and-solve approach is attributed to a 2006 paper by Sarlos:
    "Improved approximation algorithms for large matrices via random
     projections." An introduction and summary of this approach can be found
     in [MT:2020, Sections 10.2 -- 10.3].
    """

    def __init__(self, sketch_op_gen, sampling_factor, lapack_driver=None,
                 overwrite_sketch=True):
        self.sketch_op_gen = sketch_op_gen
        self.sampling_factor = sampling_factor
        self.lapack_driver = lapack_driver
        self.overwrite_sketch = overwrite_sketch

    def exec(self, A, b, tol, iter_lim, rng):
        if not np.isnan(tol):
            msg = """
            This OverLstsqSolver implementation cannot directly control
            approximation error. Parameter "tol" is being ignored.
            """
            warnings.warn(msg)
        if iter_lim > 1:
            msg = """
            This OverLstsqSolver implementation is not iterative.
            Parameter "iter_lim" is being ignored.
            """
            warnings.warn(msg)
        n_rows, n_cols = A.shape
        d = dim_checks(self.sampling_factor, n_rows, n_cols)
        rng = np.random.default_rng(rng)
        S = self.sketch_op_gen(d, n_rows, rng)
        A_ske = S @ A
        b_ske = S @ b
        res = la.lstsq(A_ske, b_ske,
                       cond=None, overwrite_a=self.overwrite_sketch,
                       overwrite_b=True, check_finite=False,
                       lapack_driver=self.lapack_driver)
        x_ske = res[0]
        return x_ske


class SAP1(OverLstsqSolver):
    """A sketch-and-precondition approach to overdetermined ordinary least
    squares. This implementation uses QR to obtain the preconditioner and
    it uses LSQR for the iterative method.

    Before starting LSQR, we run a basic sketch-and-solve (for free, given
    our QR decomposition of the sketched data matrix) to obtain a solution
    x_ske. If ||A x_ske - b||_2 < ||b||_2, then we initialize LSQR at x_ske.

    This implementation assumes A is full rank.

    References
    ----------
    This implementation was inspired by Blendenpik (AMT:2010). The differences
    relative to the official Blendenpik algorithm [AMT:2010, Algorithm 1] are

        (1) We make no assumption on the distribution of the sketching matrix
            which is used to form the preconditioner. Blendenpik only used
            SRTTs (Walsh-Hadamard, discrete cosine, discrete Hartley).

        (2) We let the user specify the exact embedding dimension, as
            floor(self.oversampling_factor * A.shape[1]).

        (3) We do not zero-pad A with additional rows. Such zero padding
            might be desirable to facilitate rapid application of SRTT
            sketching operators. It is possible to implement an SRTT operator
            so that it performs zero-padding internally.

        (4) We do not perform any checks on the quality of the preconditioner.

        (5) We initialize the iterative solver (LSQR) at the better of the two
            solutions given by either the zero vector or the output of
            sketch-and-solve.
    """

    def __init__(self, sketch_op_gen, sampling_factor: int):
        self.sketch_op_gen = sketch_op_gen
        self.sampling_factor = sampling_factor

    def exec(self, A, b, tol, iter_lim, rng):
        n_rows, n_cols = A.shape
        d = int(self.sampling_factor * n_cols)
        assert d < n_rows
        if not np.isnan(tol):
            assert tol >= 0
            assert tol < np.inf
        rng = np.random.default_rng(rng)
        S = self.sketch_op_gen(d, n_rows, rng)
        A_ske = S @ A
        # noinspection PyTupleAssignmentBalance
        Q, R = la.qr(A_ske, overwrite_a=True, mode='economic')
        b_ske = S @ b
        x_ske = la.solve_triangular(R, Q.T @ b_ske, lower=False)
        x0 = None
        if np.linalg.norm(A @ x_ske - b) < np.linalg.norm(b):
            x0 = x_ske
        res = de.upper_tri_precond_lsqr(A, b, R, tol, iter_lim, x0=x0)
        x_ske = res[0]
        return x_ske


class SAP2(OverLstsqSolver):
    """A sketch-and-precondition approach to overdetermined ordinary least
    squares. This implementation uses the SVD to obtain the preconditioner
    and it uses LSQR for the iterative method.

    Before starting LSQR, we run a basic sketch-and-solve (for free, given
    our SVD of the sketched data matrix) to obtain a solution x_ske.
    If ||A x_ske - b||_2 < ||b||_2, then we initialize LSQR at x_ske.

    This implementation does not require that A is full-rank.

    References
    ----------
    This implementation was inspired by LSRN. The differences relative to the
    official LSRN algorithm [MSM:2014, Algorithm 1] are

        (1) We make no assumption on the distribution of the sketching operator.
            LSRN uses a Gaussian sketching operator.

        (2) We provide the option of intelligently initializing the iterative
            solver (LSQR) with the better of the two solutions given by the
            zero vector and the result of sketch-and-solve.
    """

    def __init__(self, sketch_op_gen, sampling_factor, smart_init):
        self.sketch_op_gen = sketch_op_gen
        self.sampling_factor = sampling_factor
        self.smart_init = smart_init

    def exec(self, A, b, tol, iter_lim, rng):
        n_rows, n_cols = A.shape
        d = dim_checks(self.sampling_factor, n_rows, n_cols)
        if not np.isnan(tol):
            assert tol >= 0
            assert tol < np.inf
        rng = np.random.default_rng(rng)
        S = self.sketch_op_gen(d, n_rows, rng)
        A_ske = S @ A
        # noinspection PyTupleAssignmentBalance
        U, sigma, Vh = la.svd(A_ske, overwrite_a=True, check_finite=False,
                              full_matrices=False)
        eps = np.finfo(float).eps
        rank = np.count_nonzero(sigma > sigma[0] * n_cols * eps)
        N = Vh[:rank, :].T / sigma[:rank]
        if self.smart_init:
            # This isn't necessarily preferable, because it changes the
            # norm of b, which affects termination criteria.
            b_ske = S @ b
            x_ske = N @ (U[:, :rank].T @ b_ske)
            b_remainder = b - A @ x_ske
            if la.norm(b_remainder, ord=2) < la.norm(b, ord=2):
                # x_ske is a better starting point than the zero vector.
                x_star = de.pinv_precond_lsqr(A, b_remainder,
                                              N, tol, iter_lim)[0]
                x_star = x_star + x_ske
            else:
                # The zero vector is at least as good as x_ske.
                x_star = de.pinv_precond_lsqr(A, b, N, tol, iter_lim)[0]
        else:
            x_star = de.pinv_precond_lsqr(A, b, N, tol, iter_lim)[0]
        return x_star