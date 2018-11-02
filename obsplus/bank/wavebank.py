"""
A local database for waveform formats.
"""
import fnmatch
import os
import re
import time
from collections import defaultdict
from functools import partial, lru_cache, reduce
from itertools import chain
from operator import add
from os.path import abspath
from pathlib import Path
from typing import Optional, Union, List, Iterable

import numpy as np
import obspy
import pandas as pd
import tables
from obspy import UTCDateTime, Stream

import obsplus
from obsplus.bank.core import _Bank
from obsplus.bank.utils import (
    summarize_trace,
    _IndexCache,
    summarize_wave_file,
    try_read_stream,
)
from obsplus.constants import (
    NSLC,
    availability_type,
    WAVEFORM_STRUCTURE,
    WAVEFORM_NAME_STRUCTURE,
    utc_time_type,
)
from obsplus.utils import (
    make_time_chunks,
    get_inventory,
    to_timestamp,
    get_progressbar,
    thread_lock_function,
)

# from obsplus.interfaces import WaveformClient

# No idea why but this needs to be here to avoid problems with pandas
assert tables.get_hdf5_version()


# ------------------------ constants


@lru_cache(maxsize=2500)
def get_regex(nslc_str):
    return fnmatch.translate(nslc_str)  # translate to re


class WaveBank(_Bank):
    """
    A class to interact with a directory of waveform files.

    `WaveBank` reads through a directory structure of waveforms files,
    collects info from each one, then creates and index to allow the files
    to be efficiently queried.

    Implements a superset of the :class:`~obsplus.interfaces.WaveformClient`
    interface.

    Parameters
    -------------
    base_path : str
        The path to the directory containing waveform files. If it does not
        exist an empty directory will be created.
    path_structure : str
        Define the directory structure of the wavebank that will be used to
        put waveforms into the directory. Characters are separated by /,
        regardless of operating system. The following words can be used in
        curly braces as data specific variables:
            year, month, day, julday, hour, minute, second, network,
            station, location, channel, time
        example : streams/{year}/{month}/{day}/{network}/{station}
        If no structure is provided it will be read from the index, if no
        index exists the default is {net}/{sta}/{chan}/{year}/{month}/{day}
    name_structure : str
        The same as path structure but for the file name. Supports the same
        variables but requires a period as the separation character. The
        default extension (.mseed) will be added. The default is {time}
        example : {seedid}.{time}
    cache_size : int
        The number of queries to store. Avoids having to read the index of
        the bank multiple times for queries involving the same start and end
        times.
    inventory : obspy.Inventory or str
        obspy.Inventory or path to stationxml to load. Only used to attach
        responses when requested.
    format : str
        The expected format for the waveform files. Any format supported by
        obspy.read is permitted. The default is mseed. Other formats will be
        tried after the default parser fails.
    ext : str or None
        The extension of the waveform files. If provided, only files with
        this extension will be read.
    """

    # index columns and types
    metadata_columns = "last_updated path_structure name_structure".split()
    index_str = tuple(NSLC)
    index_float = ("starttime", "endtime")
    index_columns = tuple(list(index_str) + list(index_float) + ["path"])
    columns_no_path = index_columns[:-1]
    gap_columns = tuple(list(columns_no_path) + ["gap_duration"])
    namespace = "/waveforms"

    # other defaults
    buffer = 10.111  # the time before and after the desired times to pull

    # dict defining lengths of str columns (after seed spec)
    min_itemsize = {"path": 79, "station": 5, "network": 2, "location": 2, "channel": 3}

    # ----------------------------- setup stuff

    def __init__(
        self,
        base_path: Union[str, Path, "WaveBank"] = ".",
        path_structure: Optional[str] = None,
        name_structure: Optional[str] = None,
        cache_size: int = 5,
        inventory: Optional[Union[obspy.Inventory, str]] = None,
        format="mseed",
        ext=None,
    ):
        if isinstance(base_path, WaveBank):
            self.__dict__.update(base_path.__dict__)
            return
        self.format = format
        self.ext = ext
        self.bank_path = abspath(base_path)
        self.inventory = get_inventory(inventory)
        # get waveforms structure based on structures of path and filename
        self.path_structure = path_structure or WAVEFORM_STRUCTURE
        self.name_structure = name_structure or WAVEFORM_NAME_STRUCTURE
        # initialize cache
        self._index_cache = _IndexCache(self, cache_size=cache_size)

    # ----------------------- index related stuff

    @property
    def last_updated(self) -> Optional[float]:
        """
        Return the last modified time stored in the index, else None.
        """
        node = self._time_node
        try:
            out = pd.read_hdf(self.index_path, node)[0]
        except (IOError, IndexError, ValueError, KeyError, AttributeError):
            out = None
        return out

    @property
    def hdf_kwargs(self) -> dict:
        """ A dict of hdf_kwargs to pass to PyTables """
        return dict(
            complib=self._complib,
            complevel=self._complevel,
            format="table",
            data_columns=list(self.index_float),
        )

    # --- properties to get hdf5 nodes / paths

    @thread_lock_function()
    def update_index(self, bar: Optional = None, min_files_for_bar: int = 5000):
        """
        Iterate files in bank and add any modified since last update to index.

        Parameters
        ----------
        bar
            An class that has an `update` and `finish` method, should behave
            the same as the progressbar.ProgressBar class. This method provides
            a way to override the default progress bar and is used only for
            hooking this class into larger (graphical) systems.
        min_files_for_bar
            Minimum number of un-indexed files required for using the
            progress bar.
        """
        self._enforce_min_version()
        num_files = sum([1 for _ in self._unindexed_file_iterator()])
        if num_files >= min_files_for_bar:
            print(f"updating or creating waveform index for {self.bank_path}")
        kwargs = {"min_value": min_files_for_bar, "max_value": num_files}
        # init progress bar
        bar = get_progressbar(**kwargs) if bar is None else bar(**kwargs)
        # loop over un-index files and add info to index
        updates = []
        for num, fi in enumerate(self._unindexed_file_iterator()):
            updates.append(summarize_wave_file(fi, format=self.format))
            # if more files are added during update this can raise
            if bar is not None:
                # with suppress(Exception):
                bar.update(num)  # ignore; progress bar isn't too important
        getattr(bar, "finish", lambda: None)()  # call finish if applicable
        if len(updates):  # flatten list and make df
            self._write_update(list(chain.from_iterable(updates)))
            # clear cache out when new traces are added
            self._index_cache.clear_cache()

    def _write_update(self, updates):
        """ convert updates to dataframe, then append to index table """
        # read in dataframe and cast to correct types
        df = pd.DataFrame.from_dict(updates)
        # ensure the bank path is not in the path column
        df["path"] = df["path"].str.replace(self.bank_path, "")
        # assert not df.duplicated().any(), "update index has duplicate entries"
        for str_index in self.index_str:
            sser = df[str_index].astype(str)
            df[str_index] = sser.str.replace("b", "").str.replace("'", "")
        for float_index in self.index_float:
            df[float_index] = df[float_index].astype(float)
        # populate index store and update metadata
        assert not df.isnull().any().any(), "null values found in index dataframe"
        with pd.HDFStore(self.index_path) as store:
            node = self._index_node
            try:
                nrows = store.get_storer(node).nrows
            except (AttributeError, KeyError):
                store.append(
                    node, df, min_itemsize=self.min_itemsize, **self.hdf_kwargs
                )
            else:
                df.index += nrows
                store.append(node, df, append=True, **self.hdf_kwargs)
            # add metadata if not in store
            if self._meta_node not in store:
                meta = self._make_meta_table()
                store.put(self._meta_node, meta, format="table")
            # update timestamp
            store.put(self._time_node, pd.Series(time.time()))

    def read_index(
        self,
        network: Optional[str] = None,
        station: Optional[str] = None,
        location: Optional[str] = None,
        channel: Optional[str] = None,
        starttime: Optional[utc_time_type] = None,
        endtime: Optional[utc_time_type] = None,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Return a subset of the index as dataframe containing the requested
        parameters.

         Parameters
        ----------
        network : str
            The network code
        station : str
            The station code
        location : str
            The location code
        channel : str
            The channel code
        starttime : float or obspy.UTCDateTime
            The desired starttime of the waveforms
        endtime : float or obspy.UTCDateTime
            The desired endtime of the waveforms
        kwargs
            kwargs are passed to pandas.read_hdf function

        Returns
        -------
        pd.DataFrame
        """
        if starttime is not None and endtime is not None:
            if starttime > endtime:
                msg = f"starttime cannot be greater than endtime"
                raise ValueError(msg)
        if not os.path.exists(self.index_path):
            self.update_index()
        # if no file was created (dealing with empty bank) return empty index
        if not os.path.exists(self.index_path):
            return pd.DataFrame(columns=self.index_columns)
        # grab index from cache
        index = self._index_cache(starttime, endtime, buffer=self.buffer, **kwargs)
        # filter and return
        filt = filter_index(index, network, station, location, channel)
        return index[filt]

    def _read_metadata(self):
        """
        Read the metadata table.
        """
        return pd.read_hdf(self.index_path, self._meta_node)

    # ------------------------ availability stuff

    def get_availability_df(self, *args, **kwargs) -> pd.DataFrame:
        """
        Create a dataframe of start and end times for specified channels.

        Returns
        -------
        pd.DataFrame

        """
        # no need to read in path, just read needed columns
        ind = self.read_index(*args, columns=self.columns_no_path, **kwargs)
        gro = ind.groupby(list(NSLC))
        min_start = gro.starttime.min().reset_index()
        max_end = gro.endtime.max().reset_index()
        return pd.merge(min_start, max_end)

    def availability(
        self,
        network: str = None,
        station: str = None,
        location: str = None,
        channel: str = None,
    ) -> availability_type:
        df = self.get_availability_df(network, station, location, channel)
        # convert timestamps to UTCDateTime objects
        df["starttime"] = df.starttime.apply(UTCDateTime)
        df["endtime"] = df.endtime.apply(UTCDateTime)
        # convert to list of tuples, return
        return df.to_records(index=False).tolist()

    # --------------------------- get gaps stuff

    def _get_gap_dfs(self, df, min_gap):
        """ function to apply to each group of seed_id dataframes """
        dd = df.sort_values(["starttime", "endtime"]).reset_index(drop=True)
        shifted_starttimes = dd.starttime.shift(-1)
        gap_index: pd.DataFrame = (dd.endtime + min_gap) < shifted_starttimes
        # create a dataframe of gaps
        df = dd[gap_index]
        df["starttime"] = dd.endtime[gap_index]
        df["endtime"] = shifted_starttimes[gap_index]
        df["gap_duration"] = df["endtime"] - df["starttime"]
        return df

    def get_gaps_df(self, *args, min_gap=1.0, **kwargs) -> pd.DataFrame:
        """
        Return a dataframe containing an entry for every gap in the archive.

        This function accepts the same input as obsplus.Sbank.read_index.

        Parameters
        ----------
        min_gap
            The minimum gap to report. Should at least be greater than
            the sample rate in order to avoid counting one sample between
            files as gaps. If files have some overlaps this parameter can
            be set to 0.

        Returns
        -------
        pd.DataFrame
        """
        index = self.read_index(*args, columns=self.columns_no_path, **kwargs)
        group = index.groupby(list(NSLC))
        func = partial(self._get_gap_dfs, min_gap=min_gap)
        out = group.apply(func).reset_index(drop=True)
        if out.empty:  # if not gaps return empty dataframe with needed cols
            return pd.DataFrame(columns=self.gap_columns)
        return out

    def get_uptime_df(self, *args, **kwargs) -> pd.DataFrame:
        """
        Get key statistics about reliability of each seed id.

        See get_index for accepted inputs.

        Returns
        -------
        pd.DateFrame
            A dataframe with each seed id, data duration, gap duration,
            availability percentage.
        """
        # get total number of seconds bank spans for each seed id
        avail = self.get_availability_df(*args, **kwargs)
        avail["duration"] = avail["endtime"] - avail["starttime"]
        # get total duration of gaps by seed id
        gaps_df = self.get_gaps_df(*args, **kwargs)
        if not gaps_df.empty:
            gap_totals = gaps_df.groupby(list(NSLC)).gap_duration.sum()
            gap_total_df = pd.DataFrame(gap_totals).reset_index()
        else:
            gap_total_df = pd.DataFrame(avail[list(NSLC)])
            gap_total_df["gap_duration"] = 0.0
        # merge gap dataframe with availability dataframe, add uptime and %
        df = pd.merge(avail, gap_total_df)
        df["uptime"] = df.duration - df.gap_duration
        df["availability"] = df["uptime"] / df["duration"]
        return df

    # ------------------------ get waveform related methods

    def get_waveforms_bulk(
        self, bulk: List[str], index: Optional[pd.DataFrame] = None, **kwargs
    ) -> Stream:
        """
        Get a large number of waveforms with a bulk request
        Parameters
        ----------
        bulk
            A list of any number of lists containing the following:
            (network, station, location, channel, starttime, endtime).
        index
            A pandas dataframe of indicies (allows avoiding reaching out
            to db multiple times).

        Returns
        -------
        obspy.Stream

        Notes
        --------
        see get_waveforms
        """
        if not bulk:  # return emtpy waveforms if empty list or None
            return obspy.Stream()

        match_chars = {"*", "?", "[", "]"}

        def _func(time, ind, df):
            """ return waveforms from df of bulk parameters """
            # print('here')
            ar = np.ones(len(ind))  # indices of ind to use to load data
            t1, t2 = time[0], time[1]
            # print(t1, t2)
            df = df[(df.t1 == time[0]) & (df.t2 == time[1])]
            # determine which columns use any matching or other select features
            uses_matches = [_column_contains(df[x], match_chars) for x in NSLC]
            match_ar = np.array(uses_matches).any(axis=0)
            df_match = df[match_ar]
            df_no_match = df[~match_ar]
            # handle columns that need matches (more expensive)
            if not df_match.empty:
                match_bulk = df_match.to_records(index=False)
                mar = np.array([filter_index(ind, *tuple(b)[:4]) for b in match_bulk])
                ar = np.logical_and(ar, mar.any(axis=0))
            # handle columns that do not need matches
            if not df_no_match.empty:
                nslc1 = set(_get_nslc(df_no_match))
                nslc2 = _get_nslc(ind)
                ar = np.logical_and(ar, nslc2.isin(nslc1))
            return self._index2stream(ind[ar], t1, t2)

        # get a dataframe of the bulk arguments
        df = pd.DataFrame(bulk, columns=list(NSLC) + ["utc1", "utc2"])
        # df = df.replace('*', '')  # Single star matches everything
        df["t1"] = df["utc1"].apply(float)
        df["t2"] = df["utc2"].apply(float)
        # read index that contains any times that might be used, or filter
        # provided index
        t1, t2 = df["t1"].min(), df["t2"].max()
        if index is not None:
            ind = index[~((index.starttime > t2) | (index.endtime < t1))]
        else:
            ind = self.read_index(starttime=t1, endtime=t2)
        # groupby.apply calls two times for each time set, avoid this.
        unique_times = np.unique(df[["t1", "t2"]].values, axis=0)
        streams = [_func(time, df=df, ind=ind) for time in unique_times]
        return reduce(add, streams)

    def get_waveforms(
        self,
        network: Optional[str] = None,
        station: Optional[str] = None,
        location: Optional[str] = None,
        channel: Optional[str] = None,
        starttime: Optional[obspy.UTCDateTime] = None,
        endtime: Optional[obspy.UTCDateTime] = None,
        attach_response: bool = False,
    ) -> Stream:
        """
        Get waveforms from the bank.

        Note: all string parameters accept
        ? and * for posix style string matching
        Parameters
        ----------
        network : str
            The network code
        station : str
            The station code
        location : str
            The location code
        channel : str
            The channel code
        starttime : float or obspy.UTCDateTime
            The desired starttime of the waveforms
        endtime : float or obspy.UTCDateTime
            The desired endtime of the waveforms
        attach_response : bool
            If True attach the response to the waveforms using the stations

        Returns
        -------
        Requested data in an obspy.Stream instance
        """
        index = self.read_index(
            network=network,
            station=station,
            location=location,
            channel=channel,
            starttime=starttime,
            endtime=endtime,
        )
        return self._index2stream(index, starttime, endtime, attach_response)

    def yield_waveforms(
        self,
        network: Optional[str] = None,
        station: Optional[str] = None,
        location: Optional[str] = None,
        channel: Optional[str] = None,
        starttime: Optional[obspy.UTCDateTime] = None,
        endtime: Optional[obspy.UTCDateTime] = None,
        attach_response: bool = False,
        duration: float = 3600.0,
        overlap: Optional[float] = None,
    ) -> Stream:
        """
        Yield waveforms from the bank. Note: all string parameters accept
        ? and * for posix style string matching.
        Parameters
        ----------
        network : str
            The network code
        station : str
            The station code
        location : str
            The location code
        channel : str
            The channel code
        starttime : float or obspy.UTCDateTime
            The desired starttime of the waveforms
        endtime : float or obspy.UTCDateTime
            The desired endtime of the waveforms
        attach_response : bool
            If True attach the response to the waveforms using the stations
        duration : float
            The duration of the streams to yield. All channels selected
            channels will be included in the waveforms.
        overlap : float
            If duration is used, the amount of overlap in yielded streams,
            added to the end of the waveforms.

        Yields
        -------
        obspy.Stream
        """
        # get times in float format
        starttime = to_timestamp(starttime, 0.0)
        endtime = to_timestamp(endtime, "2999-01-01")
        # read in the whole index df
        index = self.read_index(
            network=network,
            station=station,
            location=location,
            channel=channel,
            starttime=starttime,
            endtime=endtime,
        )
        # adjust start/end times
        starttime = max(starttime, index.starttime.min())
        endtime = min(endtime, index.endtime.max())
        # chunk time and iterate over chunks
        time_chunks = make_time_chunks(starttime, endtime, duration, overlap)
        for t1, t2 in time_chunks:
            con1 = (index.starttime - self.buffer) > t2
            con2 = (index.endtime + self.buffer) < t1
            ind = index[~(con1 | con2)]
            if not len(ind):
                continue
            yield self._index2stream(ind, t1, t2, attach_response)

    def get_waveforms_by_seed(
        self,
        seed_id: Union[List[str], str],
        starttime: UTCDateTime,
        endtime: UTCDateTime,
        attach_response: bool = False,
    ) -> Stream:
        """
        Get waveforms based on a single seed_id or a list of seed_ids.

        Seed ids have the following form: network.station.location.channel,
        it does not yet support usage of wildcards.

        Parameters
        ----------
        seed_id
            A single seed id or sequence of ids
        starttime
            The beginning of time to pull
        endtime
            The end of the time to pull
        attach_response
            If True, and if a an stations is attached to the bank, attach
            the response to the waveforms before returning.

        Returns
        -------
        Stream
        """
        seed_id = [seed_id] if isinstance(seed_id, str) else seed_id
        index = self._read_index_by_seed(seed_id, starttime, endtime)
        return self._index2stream(index, starttime, endtime, attach_response)

    def _read_index_by_seed(self, seed_id, starttime, endtime):
        """ read the index by seed_ids """
        if not os.path.exists(self.index_path):
            self.update_index()
        index = self._index_cache(starttime, endtime, buffer=self.buffer)
        seed = (
            index.network
            + "."
            + index.station
            + "."
            + index.location
            + "."
            + index.channel
        )
        return index[seed.isin(seed_id)]

    # ----------------------- deposit waveforms methods

    def put_waveforms(self, stream: obspy.Stream, name=None):
        """
        Add the waveforms in a waveforms to the bank.

        Parameters
        ----------
        stream
            An obspy waveforms object to add to the bank
        name
            Name of file, if None it will be determined based on contents

        """
        st_dic = defaultdict(lambda: [])
        # iter the waveforms and group by common paths
        for tr in stream:
            summary = summarize_trace(
                tr,
                name=name,
                path_struct=self.path_structure,
                name_struct=self.name_structure,
            )
            path = os.path.join(self.bank_path, summary["path"])
            st_dic[path].append(tr)
        # iter all the unique paths and save
        for path, tr_list in st_dic.items():
            # make the dir structure of it doesn't exist
            if not os.path.exists(os.path.dirname(path)):
                os.makedirs(os.path.dirname(path))
            stream = obspy.Stream(traces=tr_list)
            # load the waveforms if the file already exists
            if os.path.exists(path):
                st_existing = obspy.read(path)
                stream += st_existing
            # polish streams and write
            stream.merge(method=1)
            stream.write(path, format="mseed")
        # update the index as the contents have changed
        if st_dic:
            self.update_index()

    # ------------------------ misc methods

    def _index2stream(
        self, index, starttime=None, endtime=None, attach_response=False
    ) -> Stream:
        """ return the waveforms in the index """
        # get abs path to each datafame
        files: pd.Series = (self.bank_path + index.path).unique()
        # iterate the files to read and try to load into waveforms
        stt = obspy.Stream()
        for st in (try_read_stream(x, format=self.format) for x in files):
            if st is not None and len(st):
                stt += st
        # filter out any traces not in index (this can happen when files hold
        # multiple traces).
        nslc = set(
            index.network
            + "."
            + index.station
            + "."
            + index.location
            + "."
            + index.channel
        )
        stt.traces = [x for x in stt if x.id in nslc]
        # trim, merge, attach response
        self._polish_stream(stt, starttime, endtime, attach_response)
        return stt

    def _polish_stream(self, st, starttime=None, endtime=None, attach_response=False):
        """
        prepare waveforms object for output by trimming to desired times,
        merging channels, and attaching responses
        """
        if not len(st):
            return st
        starttime = starttime or min([x.stats.starttime for x in st])
        endtime = endtime or max([x.stats.endtime for x in st])
        # trim
        st.trim(
            starttime=obspy.UTCDateTime(starttime), endtime=obspy.UTCDateTime(endtime)
        )
        if attach_response:
            st.attach_response(self.inventory)
        try:
            st.merge(method=1)
        except Exception:  # cant be more specific, obspy raises Exception
            pass  # TODO write test for this
        st.sort()

    def get_service_version(self):
        """ Return the version of obsplus """
        return obsplus.__version__


# --- auxiliary functions


def _get_nslc(df):
    """ Given a dataframe with columns network, station, location, channel
    return a series with seed ids. """

    # first replace None or other empty-ish codes with empty str
    dfs = [df[x].replace({None: "", "--": ""}) for x in NSLC]
    # concat columns and return (this is ugly but the fastest way to do it)
    return dfs[0] + "." + dfs[1] + "." + dfs[2] + "." + dfs[3]


def filter_index(index, net, sta, loc, chan, start=None, end=None):
    """ return an array of 1 and 0 of the same shape len as df, 1 means
    it meets filter reqs, 0 means it does not """
    # get a dict of query params
    query = dict(network=net, station=sta, location=loc, channel=chan)
    # divide queries into match type (str) and sequences (eg lists)
    match_query = {k: v for k, v in query.items() if isinstance(v, str)}
    sequence_query = {
        k: v for k, v in query.items() if k not in match_query and v is not None
    }
    # get a blank index of True for filters
    bool_index = np.ones(len(index), dtype=bool)
    # filter on string matching
    for key, val in match_query.items():
        regex = get_regex(val)
        new = index[key].str.match(regex).values
        bool_index = np.logical_and(bool_index, new)
    for key, val in sequence_query.items():
        bool_index = np.logical_and(bool_index, index[key].isin(val))
    if start is not None or end is not None:
        t1 = UTCDateTime(start).timestamp if start is not None else -1 * np.inf
        t2 = UTCDateTime(end).timestamp if end is not None else np.inf
        in_time = ~((index.endtime < t1) | (index.starttime > t2))
        bool_index = np.logical_and(bool_index, in_time.values)
    return bool_index


def _column_contains(ser: pd.Series, str_sequence: Iterable[str]) -> pd.Series:
    """ Test if a str series contains any values in a sequence """
    safe_matches = {re.escape(x) for x in str_sequence}
    return ser.str.contains("|".join(safe_matches)).values