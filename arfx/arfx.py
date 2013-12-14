# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""
Code for moving data in and out of arf containers.  There are some
function entry points for performing common tasks, and several script
entry points.

Functions
=====================
add_entries:      add entries from various containers to an arf file
extract_entries:  extract entries from arf file to various containers
delete_entries:   delete entries from arf file
list_entries:     generate a list of all the entries/channels in a file

Scripts
=====================
arfx:      general-purpose compression/extraction utility with tar-like syntax
"""

__version__ = "2.1.0"

import os
import sys
import posixpath
import argparse
import arf
from . import io

# template for extracted files
default_extract_template = "{entry}_{channel}.wav"
# template for created entries
default_entry_template = "{base}_{index:04}"

def entry_repr(entry):
    from h5py import h5t
    attrs = entry.attrs
    datatypes = arf.DataTypes._todict()
    out = "%s" % (entry.name)
    for k, v in attrs.items():
        if k.isupper(): continue
        if k == 'timestamp':
            out += "\n  timestamp : %s" % arf.timestamp_to_datetime(v).strftime("%Y-%m-%d %H:%M:%S.%f")
        elif k == 'uuid':
            out += "\n  uuid : %s" % arf.get_uuid(entry)
        else:
            out += "\n  %s : %s" % (k, v)
    for name, dset in entry.iteritems():
        out += "\n  /%s :" % name
        if isinstance(dset.id.get_type(), h5t.TypeVlenID):
            out += " vlarray"
        else:
            out += " array %s" % (dset.shape,)
            if 'sampling_rate' in dset.attrs:
                out += " @ %.1f/s" % dset.attrs['sampling_rate']
            if dset.dtype.names is not None:
                out += " (compound type)"

        out += ", units '%s'" % dset.attrs.get('units', '')
        out += ", type %s"  % datatypes[dset.attrs.get('datatype', arf.DataTypes.UNDEFINED)]
        if dset.compression: out += " [%s%d]" % (dset.compression, dset.compression_opts)
    return out


def parse_name_template(node, template, index=0, default="NA"):
    """ Generates names for output files using a template and the entry/dataset attributes

    see http://docs.python.org/library/string.html#format-specification-mini-language for template formatting

    dset - a dataset object
    template - string with formatting codes, e.g. {animal}
               Values are looked up in the dataset attributes, and then the parent entry attributes.
               (entry) and (channel) refer to the name of the entry and dataset
    index - value to insert for {index} key (usually the index of the entry in the file)
    default - value to replace missing keys with
    """
    from h5py import Group, Dataset
    import posixpath as pp
    from string import Formatter
    f = Formatter()
    values = dict()
    entry = dset = None
    if isinstance(node, Group):
        entry = node
    elif isinstance(node, Dataset):
        dset = node
        entry = dset.parent

    try:
        for lt, field, fs, c in f.parse(template):
            if field is None:
                continue
            elif field == "entry":
                if not entry:
                    raise ValueError, "can't resolve {entry} field for %s" % node
                values[field] = pp.basename(entry.name)
            elif field == "channel":
                if not dset:
                    raise ValueError, "can't resolve {channel} field for %s" % node
                values[field] = pp.basename(dset.name)
            elif field == "index":
                values[field] = index
            elif dset is not None and hasattr(dset, field):
                values[field] = getattr(dset, field)
            elif dset is not None and field in dset.attrs:
                values[field] = dset.attrs[field]
            elif entry is not None and hasattr(entry, field):
                values[field] = getattr(entry, field)
            elif entry is not None and field in entry.attrs:
                values[field] = entry.attrs[field]
            else:
                values[field] = default
        if values:
            return f.format(template, **values)
        else:
            return template  # no substitutions were made
    except ValueError, e:
        raise ValueError("template error: " + e.message)


def iter_entries(src, cbase='pcm'):
    """Iterate through the entries and channels of a data source.

    Yields (data, entry index, entry name,)
    """
    fp = io.open(src, 'r')
    fbase = os.path.splitext(os.path.basename(src))[0]
    nentries = getattr(fp, 'nentries', 1)
    for entry in xrange(nentries):
        try:
            fp.entry = entry
        except:
            pass

        if nentries == 1:
            yield fp, entry, fbase
        else:
            ename = default_entry_template.format(base=fbase, index=entry)
            yield fp, entry, ename


def add_entries(tgt, files, **options):
    """
    Add data to a file. This is a general-purpose function that will
    iterate through the entries in the source files (or groups of
    files) and add the data to the target file.  The source data can
    be in any file format understood by io.open.

    Additional keyword arguments specify metadata on the newly created
    entries.
    """
    from h5py import Group
    compress = options.get('compress', None)
    ebase = options.get('template', None)
    metadata = options.get('attrs', None) or dict()
    datatype = options.get('datatype', arf.DataTypes.UNDEFINED)
    chan = "pcm"                # only pcm data can be imported

    if len(files) == 0:
        raise ValueError, "must specify one or more input files"

    with arf.open_file(tgt, 'a') as arfp:
        arf.check_file_version(arfp)
        arf.set_attributes(arfp, file_creator="org.meliza.arfx/arfx " + __version__, overwrite=False)
        for f in files:
            try:
                for fp, entry_index, entry_name in iter_entries(f):
                    timestamp = getattr(fp, 'timestamp', None)
                    if timestamp is None:
                        # kludge for ewave
                        if hasattr(fp, 'fp') and hasattr(fp.fp, 'fileno'):
                            timestamp = os.fstat(fp.fp.fileno()).st_mtime
                        else:
                            raise ValueError("%s/%d missing required timestamp" %
                                             (f, entry_index))
                    if not hasattr(fp, 'sampling_rate'):
                        raise ValueError("%s/%d missing required sampling_rate attribute" %
                                         (f, entry_index))

                    if ebase is not None:
                        entry_name = default_entry_template.format(
                            base=ebase,
                            index=arf.count_children(arfp, Group))
                    entry = arf.create_entry(arfp, entry_name, timestamp,
                                             entry_creator="org.meliza.arfx/arfx " + __version__,
                                             **metadata)
                    arf.create_dataset(entry, chan, fp.read(),
                                       datatype=datatype,
                                       sampling_rate=fp.sampling_rate,
                                       compression=compress,
                                       source_file=f,
                                       source_entry=entry_index)
                    if options['verbose']:
                        print "%s/%d -> /%s/%s" % (f, entry_index, entry_name, chan)
            except Exception, e:
                print "arfx: input error: %s" % e


def create_and_add_entries(tgt, files, **options):
    """ Add data to a new file. If the file exists it's deleted """
    if os.path.exists(tgt):
        os.remove(tgt)
    add_entries(tgt, files, **options)


def extract_entries(src, entries, **options):
    """
    Extract entries from a file.  The format and naming of the output
    containers is determined automatically from the name of the entry
    and the type of data.

    entries: list of the entries to extract. can be None, in which
             case all the entries are extracted
    entry_base: if specified, name the output files sequentially
    """
    if not os.path.exists(src):
        raise IOError, "the file %s does not exist" % src

    if len(entries) == 0:
        entries = None
    ebase = options.get('template', None)

    with arf.open_file(src, 'r') as arfp:
        try:
            arf.check_file_version(arfp)
        except Warning as e:
            print "arfx: warning: %s" % e
        for index, ename in enumerate(arf.keys_by_creation(arfp)):
            entry = arfp[ename]
            attrs = dict(entry.attrs)
            if entries is None or ename in entries:
                for channel in entry:
                    dset = entry[channel]
                    attrs.update(
                        nchannels=dset.shape[1] if len(dset.shape) > 1 else 1,
                        dtype=dset.dtype,
                        **dset.attrs)
                    fname = parse_name_template(dset,
                                                ebase or default_extract_template,
                                                index=index)
                    try:
                        fp = io.open(fname, 'w', **attrs)
                    except TypeError as e:
                        raise IOError("can't write to file '%s' - %s" % (fname, e))
                    dtype, stype, ncols = arf.dataset_properties(dset)
                    if dtype != 'sampled':
                        continue

                    fp.write(dset)
                    if 'timestamp' in entry.attrs:
                        fp.timestamp = entry.attrs['timestamp'][0]
                    else:
                        # try to set the modification time
                        pass

                    if options['verbose']:
                        print "%s -> %s" % (dset.name, fname)


def delete_entries(src, entries, **options):
    """
    Delete one or more entries from a file.

    entries: list of the entries to delete
    repack: if True (default), repack the file afterward to reclaim space
    """
    if not os.path.exists(src):
        raise IOError, "the file %s does not exist" % src
    if entries is None or len(entries) == 0:
        return

    with arf.open_file(src, 'r+') as arfp:
        arf.check_file_version(arfp)
        count = 0
        for entry in entries:
            if entry in arfp:
                try:
                    del arfp[entry]
                    count += 1
                    if options['verbose']:
                        print "deleted /%s" % entry
                except Exception, e:
                    print "Error deleting %s: %s" % (entry, e)
            elif options['verbose']:
                print "/%s: no such entry" % entry
    if count > 0 and options['repack']:
        repack_file(src, **options)


def copy_entries(tgt, files, **options):
    """
    Copy data from another arf file. Arguments can refer to entire arf
    files (just the filename) or specific entries (using path
    notation).  Record IDs and all other metadata are copied with the entry.

    entry_base: if specified, rename entries sequentially in target file
    """
    from tools import memoized
    from h5py import Group
    ebase = options.get('template', None)
    acache = memoized(arf.open_file)

    with arf.open_file(tgt, 'a') as arfp:
        arf.check_file_version(arfp)
        for f in files:
            # this is a bit tricky:
            # file.arf is a file; file.arf/entry is entry
            # dir/file.arf is a file; dir/file.arf/entry is entry
            # on windows, dir\file.arf/entry is an entry
            pn, fn = posixpath.split(f)
            if os.path.isfile(f):
                it = ((f, entry) for ename, entry in acache(f).items())
            elif os.path.isfile(pn):
                fp = acache(pn)
                if fn in fp:
                    it = ((pn, fp[fn]),)
                else:
                    print "Error: no such entry %s" % f
                    continue
            else:
                print "Error: %s does not exist" % f
                continue

            for fname, entry in it:
                try:
                    if ebase is not None:
                        entry_name = default_entry_template.format(
                            base=ebase, index=arf.count_children(arfp, Group))
                    else:
                        entry_name = posixpath.basename(entry.name)
                    arfp.copy(entry, arfp, name=entry_name)
                    if options['verbose']:
                        print "%s%s -> %s/%s" % (fname, entry.name, tgt, entry_name)
                except Exception, e:
                    print "arfx: copy error for %s%s: %s" % (fname, entry.name, e)


def list_entries(src, entries, **options):
    """
    List the contents of the file, optionally restricted to specific entries

    entries: if None or empty, list all entries; otherwise only list entries
             that are in this list (more verbosely)
    """
    if not os.path.exists(src):
        raise IOError, "the file %s does not exist" % src
    print "%s:" % src
    with arf.open_file(src, 'r') as arfp:
        try:
            arf.check_file_version(arfp)
        except Warning as e:
            print "arfx: warning: %s" % e
        if entries is None or len(entries) == 0:
            try:
                it = arf.keys_by_creation(arfp)
            except RuntimeError:
                it = iter(arfp)
            for name in it:
                entry = arfp[name]
                if options.get('verbose', False):
                    print entry_repr(entry)
                else:
                    print "%s: %d channel%s" % (entry.name, len(entry),
                                                arf.pluralize(len(entry)))
        else:
            for ename in entries:
                if ename in arfp:
                    print entry_repr(arfp[ename])


def update_entries(src, entries, **options):
    """
    Update metadata on one or more entries

    entries: if None or empty, updates all entries. In this case, if the
             name parameter is set, the entries are renamed sequentially
    """
    if not os.path.exists(src):
        raise IOError, "the file %s does not exist" % src
    ebase = options.get('template', None)
    if (entries is None or len(entries) == 0) and ebase is not None:
        if ebase.find('{') < 0:
            raise ValueError, "with multiple entries, template needs to have {} formatter fields"
    metadata = options.get('attrs', None) or dict()
    if 'datatype' in options:
        metadata['datatype'] = options['datatype']

    with arf.open_file(src, 'r+') as arfp:
        try:
            arf.check_file_version(arfp)
        except Warning as e:
            print "arfx: warning: %s" % e
        for i, entry in enumerate(arfp):
            if entries is None or len(entries) == 0 or posixpath.relpath(entry) in entries:
                enode = arfp[entry]
                if options.get('verbose', False):
                    print "vvvvvvvvvv"
                    print entry_repr(enode)
                    print "**********"
                if ebase:
                    name = parse_name_template(enode, ebase, index=i)
                    arfp[name] = enode
                    del arfp[entry]  # entry object should remain valid
                arf.set_attributes(enode, **metadata)
                if options.get('verbose', False):
                    print entry_repr(enode)
                    print "^^^^^^^^^^"


def repack_file(path, **options):
    """ Call h5repack on a list of files to repack them """
    from shutil import rmtree, copy
    from tempfile import mkdtemp

    cmd = '/usr/bin/env h5repack '
    compress = options.get('compress', False)
    if compress:
        cmd += "-f SHUF -f GZIP=%d " % compress
    try:
        tdir = mkdtemp()
        if options['verbose']:
            sys.stdout.write("Repacking %s..." % path)
            sys.stdout.flush()
        fdir, fbase = os.path.split(path)
        os.system(cmd + path + " " + os.path.join(tdir, fbase))
        copy(os.path.join(tdir, fbase), path)
        if options['verbose']:
            sys.stdout.write("done\n")
    finally:
        rmtree(tdir)


def upgrade_file(path, *args, **options):
    from . import migrate
    migrate.migrate_file(path)
    repack_file(path, **options)


class ParseKeyVal(argparse.Action):

    def __call__(self, parser, namespace, arg, option_string=None):
        kv = getattr(namespace, self.dest)
        if kv is None:
            kv = dict()
        if not arg.count('=') == 1:
            print >> sys.stderr, "-k %s argument badly formed; needs key=value" % arg
        else:
            key, val = arg.split('=')
            kv[key] = val
        setattr(namespace, self.dest, kv)


class ParseDataType(argparse.Action):

    def __call__(self, parser, namespace, arg, option_string=None):
        if not arg.isdigit():
            arg = arf.DataTypes._fromstring(arg)
            if arg is None:
                raise ValueError, "%s is not a valid data type" % arg
        setattr(namespace, self.dest, int(arg))


def arfx():

    p = argparse.ArgumentParser(description='copy data in and out of ARF files')
    p.add_argument('entries', nargs='*')
    p.add_argument('--version', action='version',
                   version='%(prog)s ' + __version__)
    p.add_argument('--help-datatypes',
                   help='print available datatypes and exit',
                   action='version', version=arf.DataTypes._doc())

    # operations
    pp = p.add_argument_group('Operations')
    g = pp.add_mutually_exclusive_group(required=True)
    g.add_argument('-A', help='copy data from another ARF file',
                   action='store_const', dest='op', const=copy_entries)
    g.add_argument('-c', help='create new file and add data',
                   action='store_const', dest='op', const=create_and_add_entries)
    g.add_argument('-r', help='add data to an existing file',
                   action='store_const', dest='op', const=add_entries)
    g.add_argument('-x', help='extract entries from a file',
                   action='store_const', dest='op', const=extract_entries)
    g.add_argument('-t', help='list contents of the file',
                   action='store_const', dest='op', const=list_entries)
    g.add_argument('-U', help='update metadata of entries',
                   action='store_const', dest='op', const=update_entries)
    g.add_argument('-d', help='delete entries',
                   action='store_const', dest='op', const=delete_entries)
    g.add_argument('--upgrade', help="migrate older ARF versions to %s" % __version__,
                   action='store_const', dest='op', const=upgrade_file)

    g = p.add_argument_group('Options')
    g.add_argument('-f', help='the ARF file to operate on', required=True,
                   metavar='FILE', dest='arffile')
    g.add_argument('-v', help='verbose output',
                   action='store_true', dest='verbose')
    g.add_argument('-n', help='name entries or files using %(metavar)s',
                   metavar='TEMPLATE', dest='template')
    g.add_argument('-T', help='specify data type (see --help-datatypes)',
                   default=arf.DataTypes.UNDEFINED, metavar='DATATYPE', dest='datatype', action=ParseDataType)
    g.add_argument('-k', help='specify attributes of entries', action=ParseKeyVal,
                   metavar="KEY=VALUE", dest='attrs')
    g.add_argument('-P', help="don't repack when deleting entries", action='store_false',
                   dest='repack')
    g.add_argument('-z', help="set compression level in ARF (default: %(default)s)", type=int,
                   default=1, dest='compress')

    args = p.parse_args()

    try:
        opts = args.__dict__.copy()
        entries = opts.pop('entries')
        args.op(args.arffile, entries, **opts)
    except Exception, e:
        print >> sys.stderr, "arfx: error: %s" % e
        if isinstance(e, DeprecationWarning):
            print >> sys.stderr, "      use arfx --upgrade to convert to version %s" % arf.spec_version
        sys.exit(-1)
    return 0

if __name__ == "__main__":
    sys.exit(arfx())

# Variables:
# End:
