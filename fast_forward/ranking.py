from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterator, Set, Union

import numpy as np
import pandas as pd

Run = Dict[str, Dict[str, Union[float, int]]]


class Ranking(object):
    """Represents rankings of documents/passages w.r.t. queries."""

    def __init__(
        self,
        df: pd.DataFrame,
        name: str = None,
        dtype: np.dtype = np.float32,
        copy: bool = True,
    ) -> None:
        """Create a ranking from an existing data frame.

        Args:
            df (pd.DataFrame): Data frame containing IDs and scores.
            name (str, optional): Method name. Defaults to None. Defaults to True.
            dtype (np.dtype, optional): How the scores should be represented in the data frame. Defaults to np.float32.
            copy (bool, optional): Whether to copy the data frame. Defaults to True.
        """
        super().__init__()
        self.name = name
        if copy:
            self._df = df.loc[:, ["q_id", "id", "score"]].copy()
        else:
            self._df = df.loc[:, ["q_id", "id", "score"]]

        self._df["score"] = self._df["score"].astype(dtype)
        self._q_ids = set(pd.unique(self._df["q_id"]))
        self.sort()

    @property
    def q_ids(self) -> Set[str]:
        """The set of (unique) query IDs in this ranking. Only queries with at least one scored document are considered.

        Returns:
            Set[str]: The query IDs.
        """
        return self._q_ids

    def attach_queries(self, queries: Dict[str, str]) -> None:
        """Attach queries to this ranking (in-place).

        Args:
            queries (Dict[str, str]): Query IDs mapped to queries.
        """
        if set(queries.keys()) != self._q_ids:
            raise ValueError("Queries are incomplete")
        q_df = pd.DataFrame(queries.items(), columns=["q_id", "query"])
        self._df = self._df.merge(q_df, how="left", on="q_id")

    def sort(self) -> None:
        """Sort the ranking by scores (in-place)."""
        self._df.sort_values(by=["q_id", "score"], inplace=True, ascending=False)
        self._df.reset_index(inplace=True, drop=True)
        self.is_sorted = True

    def cut(self, cutoff: int) -> None:
        """For each query, remove all but the top-k scoring documents/passages.

        Args:
            cutoff (int): Number of best scores per query to keep (k).
        """
        self._df = self._df.groupby("q_id").head(cutoff).reset_index(drop=True)

    def __getitem__(self, q_id: str) -> Dict[str, float]:
        """Return the ranking for a query.

        Args:
            q_id (str): The query ID.

        Returns:
            Dict[str, float]: Document/passage IDs mapped to scores.
        """
        return dict(self._df[self._df["q_id"] == q_id][["id", "score"]].values)

    def __len__(self) -> int:
        """Return the number of queries.

        Returns:
            int: The number of queries.
        """
        return len(self._q_ids)

    def __iter__(self) -> Iterator[str]:
        """Yield all query IDs.

        Yields:
            str: The query IDs.
        """
        yield from self._q_ids

    def __contains__(self, key: object) -> bool:
        """Check whether a query ID is in the ranking.

        Args:
            key (object): The query ID.

        Returns:
            bool: Wherther the query ID has associated document/passage IDs.
        """
        return key in self._q_ids

    def __eq__(self, o: object) -> bool:
        """Check if this ranking is identical to another one. Only takes IDs and scores into account.

        Args:
            o (object): The other ranking.

        Returns:
            bool: Whether the two rankings are identical.
        """
        return self._df.set_index(["q_id", "id"])["score"].equals(
            o._df.set_index(["q_id", "id"])["score"]
        )

    def __repr__(self) -> str:
        """Return the run a string representation of this ranking.

        Returns:
            str: The string representation.
        """
        return self._df.__repr__()

    def save(
        self,
        target: Path,
    ) -> None:
        """Save the ranking in a TREC runfile.

        Args:
            target (Path): Output file.
        """
        df_ranks = self._df.groupby("q_id").cumcount().to_frame()
        df_ranks.columns = ("rank",)
        df_out = self._df.join(df_ranks)
        df_out["name"] = str(self.name)
        df_out["q0"] = "Q0"

        target.parent.mkdir(parents=True, exist_ok=True)
        df_out.to_csv(
            target,
            sep="\t",
            columns=["q_id", "q0", "id", "rank", "score", "name"],
            index=False,
            header=False,
        )

    @classmethod
    def from_run(
        cls, run: Run, name: str = None, dtype: np.dtype = np.float32
    ) -> "Ranking":
        """Create a Ranking object from a TREC run.

        Args:
            run (Run): TREC run.
            dtype (np.dtype, optional): How the score should be represented in the data frame. Defaults to np.float32.

        Returns:
            Ranking: The resulting ranking.
        """
        df = pd.DataFrame.from_dict(run).stack().reset_index()
        df.columns = ("id", "q_id", "score")
        return cls(df, name=name, dtype=dtype, copy=False)

    @classmethod
    def from_file(cls, f: Path, dtype: np.dtype = np.float32) -> "Ranking":
        """Create a Ranking object from a runfile in TREC format.

        Args:
            f (Path): TREC runfile to read.
            dtype (np.dtype, optional): How the score should be represented in the data frame. Defaults to np.float32.

        Returns:
            Ranking: The resulting ranking.
        """
        df = pd.read_csv(
            f,
            delim_whitespace=True,
            skipinitialspace=True,
            header=None,
            names=["q_id", "q0", "id", "rank", "score", "name"],
        )
        return cls(df, name=df["name"][0], dtype=dtype, copy=False)


def interpolate(
    r1: Ranking, r2: Ranking, alpha: float, name: str = None, sort: bool = True
) -> Ranking:
    """Interpolate scores. For each query-doc pair:
        * If the pair has only one score, ignore it.
        * If the pair has two scores, interpolate: r1 * alpha + r2 * (1 - alpha).

    Args:
        r1 (Ranking): Scores from the first retriever.
        r2 (Ranking): Scores from the second retriever.
        alpha (float): Interpolation weight.
        name (str, optional): Ranking name. Defaults to None.
        sort (bool, optional): Whether to sort the documents by score. Defaults to True.

    Returns:
        Ranking: Interpolated ranking.
    """
    assert r1.q_ids == r2.q_ids
    results = defaultdict(dict)
    for q_id in r1:
        for doc_id in r1[q_id].keys() & r2[q_id].keys():
            results[q_id][doc_id] = (
                alpha * r1[q_id][doc_id] + (1 - alpha) * r2[q_id][doc_id]
            )
    return Ranking.from_run(results, name=name, sort=sort)
