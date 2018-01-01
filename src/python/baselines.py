import os
import sys
import subprocess

from tqdm import tqdm

from Bio.Seq import Seq
from Bio import SeqIO, SearchIO
from Bio.SeqRecord import SeqRecord

from Bio.Blast.Applications import NcbiblastpCommandline

from .preprocess import *


from itertools import cycle
import matplotlib.pyplot as plt

from pymongo import MongoClient

from tempfile import gettempdir
tmp_dir = gettempdir()

from shutil import copyfile

import argparse


t0 = datetime.datetime(2014, 1, 1, 0, 0)
t1 = datetime.datetime(2014, 9, 1, 0, 0)

ASPECT = 'F'
ONTO = None
PRIOR = {}
THRESHOLDS = np.arange(0.1, 1, 0.02)

# unparseables = ["Cross_product_review", "Involved_in",
#                 "gocheck_do_not_annotate",
#                 "Term not to be used for direct annotation",
#                 "gocheck_do_not_manually_annotate",
#                 "Term not to be used for direct manual annotation",
#                 "goslim_aspergillus", "Aspergillus GO slim",
#                 "goslim_candida", "Candida GO slim",
#                 "goslim_generic", "Generic GO slim",
#                 "goslim_metagenomics", "Metagenomics GO slim",
#                 "goslim_pir", "PIR GO slim",
#                 "goslim_plant", "Plant GO slim",
#                 "goslim_pombe", "Fission yeast GO slim",
#                 "goslim_yeast", "Yeast GO slim",
#                 "gosubset_prok", "Prokaryotic GO subset",
#                 "mf_needs_review", "Catalytic activity terms in need of attention",
#                 "termgenie_unvetted",
#                 "Terms created by TermGenie that do not follow a template and require additional vetting by editors",
#                 "virus_checked", "Viral overhaul terms"]


def init_GO(asp=ASPECT, src=None):
    global ONTO, ASPECT
    if src: set_obo_src(src)
    ASPECT = asp
    ONTO = get_ontology(asp)


def add_arguments(parser):
    parser.add_argument("--mongo_url", type=str, default='mongodb://localhost:27017/',
                        help="Supply the URL of MongoDB")


def load_all_data():
    mf, _ = load_data(db, asp='F', codes=exp_codes)
    cc, _ = load_data(db, asp='C', codes=exp_codes)
    bp, _ = load_data(db, asp='P', codes=exp_codes)
    return mf, cc, bp


# def load_evaluation_data(setting="cafa2"):
#     if setting == "cafa2":
#         data_root = "data/cafa/CAFA2Supplementary_data/data/"
#
#         go_pth = os.path.join(data_root, "ontology", "go_20130615-termdb.obo")
#         go_copy_pth = "%s.copy" % go_pth
#
#         with open(go_pth, "rt") as fin:
#             with open(go_copy_pth, "wt") as fout:
#                 for line in fin:
#                     for term in unparseables:
#                         line = line.replace(term, '')
#
#                     fout.write(line)
#
#         init_GO(ASPECT, go_copy_pth)
#         fpath = os.path.join(data_root, "GO-t0", "goa.go.%s" % GoAspect(ASPECT))
#         num_mapping = count_lines(fpath, sep=bytes('\n', 'utf8'))
#         src_mapping = open(fpath, 'r')
#         ref2go, _ = MappingFileLoader(src_mapping, num_mapping).load()
#
#         trg2seq = dict()
#         for domain in ["archaea", "bacteria", "eukarya"]:
#             targets_dir = os.path.join(data_root, "CAFA2-targets", domain)
#             trg2seq.update(load_cafa2_targets(targets_dir))
#         trg2go = dict()
#         annots_dir = os.path.join(data_root, "benchmark", "groundtruth")
#         fpath = os.path.join(annots_dir, "propagated_%s.txt" % GoAspect(ASPECT))
#         num_mapping = count_lines(fpath, sep=bytes('\n', 'utf8'))
#         src_mapping = open(fpath, 'r')
#         d1, _ = MappingFileLoader(src_mapping, num_mapping).load()
#         trg2go.update(d1)
#         return trg2seq, trg2go
#     elif setting == "cafa3":
#         pass
#
#     else:
#         print("Unknown evaluation setting")
#     pass


# def load_cafa2_targets(targets_dir):
#
#     trg2seq = dict()
#
#     for fname in os.listdir(targets_dir):
#         print("\nLoading: %s" % fname)
#         fpath = "%s/%s" % (targets_dir, fname)
#         num_seq = count_lines(fpath, sep=bytes('>', 'utf8'))
#         fasta_src = parse_fasta(open(fpath, 'r'), 'fasta')
#         trg2seq.update(FastaFileLoader(fasta_src, num_seq).load())
#
#     return trg2seq


def load_training_and_validation(db, limit=None):
    q_train = {'DB': 'UniProtKB',
               'Evidence': {'$in': exp_codes},
               'Date':  {"$lte": t0},
               'Aspect': ASPECT}

    sequences_train, annotations_train, _ = _get_labeled_data(db, q_train, None)

    q_valid = {'DB': 'UniProtKB',
               'Evidence': {'$in': exp_codes},
               'Date':  {"$gt": t0, "$lte": t1},
               'Aspect': ASPECT}

    sequences_valid, annotations_valid, _ = _get_labeled_data(db, q_valid, limit)
    forbidden = set(sequences_train.keys())
    sequences_valid = {k: v for k, v in sequences_valid.items() if k not in forbidden}
    annotations_valid = {k: v for k, v in annotations_valid.items() if k not in forbidden}

    return sequences_train, annotations_train, sequences_valid, annotations_valid


def _get_labeled_data(db, query, limit, propagate=True):

    c = limit if limit else db.goa_uniprot.count(query)
    s = db.goa_uniprot.find(query)
    if limit: s = s.limit(limit)

    seqid2goid, goid2seqid = GoAnnotationCollectionLoader(s, c, ASPECT).load()

    query = {"_id": {"$in": unique(list(seqid2goid.keys())).tolist()}}
    num_seq = db.uniprot.count(query)
    src_seq = db.uniprot.find(query)

    seqid2seq = UniprotCollectionLoader(src_seq, num_seq).load()

    if propagate:
        for k, v in seqid2goid.items():
            annots = ONTO.sort(ONTO.augment(v))
            annots.pop()  # pop the root
            seqid2goid[k] = set(annots)

    return seqid2seq, seqid2goid, goid2seqid


def _prepare_naive(reference):
    global PRIOR
    go2count = {}
    for _, go_terms in reference.items():
        for go in go_terms:
            if go in go2count:
                go2count[go] += 1
            else:
                go2count[go] = 1
    total = len(reference)
    PRIOR = {go: count/total for go, count in go2count.items()}


def _naive(target, reference):
    global PRIOR
    return PRIOR


def _prepare_blast(sequences):
    records = [SeqRecord(Seq(seq), id) for id, seq in sequences.items()]
    blastdb_pth = os.path.join(tmp_dir, 'blast-%s' % GoAspect(ASPECT))
    SeqIO.write(records, open(blastdb_pth, 'w+'), "fasta")
    os.system("makeblastdb -in %s -dbtype prot" % blastdb_pth)


def _blast(target, reference, topn=None, choose_max_prob=True):

    query_pth = os.path.join(tmp_dir, 'query-%s.fasta' % GoAspect(ASPECT))
    output_pth = os.path.join(tmp_dir, "blastp-%s.out" % GoAspect(ASPECT))
    database_pth = os.path.join(tmp_dir, 'blast-%s' % GoAspect(ASPECT))

    SeqIO.write(SeqRecord(Seq(target), "QUERY"), open(query_pth, 'w+'), "fasta")

    cline = NcbiblastpCommandline(query=query_pth, db=database_pth, out=output_pth,
                                  outfmt=5, evalue=0.001, remote=False, ungapped=False)

    child = subprocess.Popen(str(cline),
                             stderr=subprocess.PIPE,
                             universal_newlines=True,
                             shell=(sys.platform != "win32"))

    handle, _ = child.communicate()
    assert child.returncode == 0

    blast_qresult = SearchIO.read(output_pth, 'blast-xml')

    annotations = {}
    for hsp in blast_qresult.hsps[:topn]:

        ident = hsp.ident_num / hsp.hit_span
        for go in reference[hsp.hit.id]:
            if go in annotations:
                annotations[go].append(ident)
            else:
                annotations[go] = [ident]

    for go, ps in annotations.items():
        if choose_max_prob:
            annotations[go] = max(ps)
        else:
            annotations[go] = 1 - np.prod([(1 - p) for p in ps])
    return annotations


def _predict(reference_annots, target_seqs, func_predict, binary_mode=False):

    pbar = tqdm(range(len(target_seqs)), desc="targets processed")

    if binary_mode:
        predictions = np.zeros((len(target_seqs), len(ONTO.classes)))
        for i, (_, seq) in enumerate(target_seqs.items()):
            preds = func_predict(seq, reference_annots)
            bin_preds = ONTO.binarize([list(preds.keys())])[0]
            for go, prob in preds.items():
                bin_preds[ONTO[go]] = prob
            predictions[i, :] = bin_preds
            pbar.update(1)
    else:
        predictions = {}
        for _, (seqid, seq) in enumerate(target_seqs.items()):
            predictions[seqid] = func_predict(seq, reference_annots)
            pbar.update(1)
    pbar.close()

    return predictions


def precision(tau, predictions, targets):
    # P = np.where(P >= tau, 1, 0)
    # ix = np.array(list(map(lambda row: np.sum(row) > 0, P)))
    # P, T = P[ix, :], T[ix, :]
    # m_th, _ = P.shape   # m(tau)
    # intersection = np.where(P + T == 2, 1, 0)
    # total = np.sum([np.sum(intersection[i, :]) / np.sum(P[i, :]) for i in range(m_th)])
    # return total / m_th

    P, T = [], []
    for seqid, annotations in predictions.items():
        preds = set([go for go, prob in annotations.items() if prob >= tau])
        if len(preds) == 0:
            continue
        P.append(preds)
        T.append(targets[seqid])

    assert len(P) == len(T)
    if len(P) == 0: return 1.0

    total = sum([len(P_i & T_i) / len(P_i) for P_i, T_i in zip(P, T)])
    return total / len(P)


def recall(tau, predictions, targets, partial_evaluation=False):
    # if partial_evaluation:
    #     ix = np.array(list(map(lambda row: np.sum(row) > 0, P)))
    #     P, T = P[ix, :], T[ix, :]        # n_e = m(0)
    # n_e, _ = T.shape    # n_e = n
    # P = np.where(P >= tau, 1, 0)
    # intersection = np.where(P + T == 2, 1, 0)
    # total = np.sum([np.sum(intersection[i, :]) / np.sum(T[i, :]) for i in range(n_e)])
    # return total / n_e

    P, T = [], []
    for seqid, annotations in predictions.items():
        preds = set([go for go, prob in annotations.items() if prob >= tau])
        if not partial_evaluation and len(annotations) == 0: continue
        P.append(preds)
        T.append(targets[seqid])

    assert len(P) == len(T)
    if len(P) == 0: return 0.0

    total = sum([len(P_i & T_i) / len(T_i) for P_i, T_i in zip(P, T)])
    return total / len(T)


def F_beta(pr, rc, beta=1):
    if rc == 0 and pr == 0:
        return np.nan
    return (1 + beta ** 2) * ((pr * rc) / (((beta ** 2) * pr) + rc))


def F_max(P, T, thresholds=THRESHOLDS):
    return np.max([F_beta(precision(th, P, T), recall(th, P, T)) for th in thresholds])


def predict(reference_seqs, reference_annots, target_seqs, method, load_file=True):
    pred_path = os.path.join(tmp_dir, 'pred-%s-%s.npy' % (method, GoAspect(ASPECT)))
    if load_file and os.path.exists(pred_path):
        return np.load(pred_path).item()
    if method == "blast":
        _prepare_blast(reference_seqs)
        predictions = _predict(reference_annots, target_seqs, _blast)
        np.save(pred_path, predictions)
        return predictions
    elif method == "naive":
        _prepare_naive(reference_annots)
        predictions = _predict(reference_annots, target_seqs, _naive)
        np.save(pred_path, predictions)
        return predictions
    else:
        print("Unknown method")


def performance(predictions, ground_truth, thresholds=THRESHOLDS):
    P, T = predictions, ground_truth
    prs = [precision(th, P, T) for th in thresholds]
    rcs = [recall(th, P, T) for th in thresholds]
    f1s = [F_beta(pr, rc) for pr, rc in zip(prs, rcs)]
    return prs, rcs, f1s


def plot_precision_recall(perf):
    # Plot Precision-Recall curve
    lw, n = 2, len(perf)
    methods = list(perf.keys())
    prs = [v[0] for v in perf.values()]
    rcs = [v[1] for v in perf.values()]
    f1s = [v[2] for v in perf.values()]

    colors = cycle(['red', 'blue', 'navy', 'turquoise', 'darkorange', 'cornflowerblue', 'teal'])

    # Plot Precision-Recall curve for each class
    plt.clf()

    for i, color in zip(range(len(methods)), colors):
        plt.plot(rcs[i], prs[i], color=color, lw=lw,
                 label='{0} (F_max = {1:0.2f})'
                 .format(methods[i], max(f1s[i])))

    plt.xlim([0.0, 1.0])
    plt.ylim([0.0, 1.05])
    plt.xlabel('Recall')
    plt.ylabel('Precision')
    plt.title(GoAspect(ASPECT))
    plt.legend(loc="lower right")
    plt.show()


def evaluate(methods, asp):
    init_GO(asp)
    lim = None
    seqs_train, annots_train, seqs_valid, annots_valid = \
        load_training_and_validation(db, lim)
    perf = {}
    for meth in methods:
        pred = predict(seqs_train, annots_train, seqs_valid, meth)
        perf[meth] = performance(pred, annots_valid)
    plot_precision_recall(perf)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    args = parser.parse_args()

    # load_evaluation_data()

    client = MongoClient(args.mongo_url)
    db = client['prot2vec']
    lim = 100
    init_GO(ASPECT)

    seqs_train, annots_train, seqs_valid, annots_valid = load_training_and_validation(db, lim)
    y_blast = predict(seqs_train, annots_train, seqs_valid, "blast")
    performance(y_blast, annots_valid)
