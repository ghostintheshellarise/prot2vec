import os
import numpy as np

from shutil import copyfile

import torch
import torch.nn as nn
from torch.autograd import Variable

from pymongo import MongoClient

import word2vec as W2V
from word2vec import vocabulary_size
from word2vec import WindowBatchLoader

from sklearn.metrics import f1_score

from tempfile import gettempdir

import argparse


# CNN Model (2 conv layer)
class CNN(nn.Module):
    def __init__(self, emb_size, win_size, hidden_size):
        super(CNN, self).__init__()
        self.win_size = win_size
        self.emb_size = emb_size
        self.emb = nn.Embedding(vocabulary_size, emb_size)
        self.layer1 = nn.Sequential(
            nn.Conv2d(1, 64, kernel_size=emb_size, padding=2),
            nn.BatchNorm2d(64),
            nn.ReLU(),
            nn.MaxPool2d(2))
        self.layer2 = nn.Sequential(
            nn.Conv2d(64, 32, kernel_size=emb_size, padding=2),
            nn.BatchNorm2d(32),
            nn.ReLU(),
            nn.MaxPool2d(2))
        self.layer3 = nn.Sequential(
            nn.Linear(160, hidden_size),
            nn.BatchNorm1d(hidden_size),
            nn.Sigmoid())
        self.fc = nn.Linear(hidden_size, vocabulary_size)
        self.sf = nn.Softmax()

    def forward(self, x):
        out = self.emb(x).unsqueeze(1)
        out = self.layer1(out)
        out = self.layer2(out)
        out = out.view(out.size(0), -1)
        out = self.layer3(out)
        out = self.fc(out)
        out = self.sf(out)
        return out


class MLP(nn.Module):

    def __init__(self, emb_size, win_size, hidden_size):
        super(MLP, self).__init__()
        self.win_size = win_size
        self.emb_size = emb_size
        self.emb = nn.Embedding(vocabulary_size, emb_size)
        self.layer1 = nn.Sequential(
            nn.Linear(2 * win_size * emb_size, hidden_size),
            nn.BatchNorm1d(hidden_size),
            nn.Sigmoid())
        self.layer2 = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2),
            nn.BatchNorm1d(hidden_size // 2),
            nn.Sigmoid())
        self.layer3 = nn.Sequential(
            nn.Linear(hidden_size // 2, hidden_size),
            nn.BatchNorm1d(hidden_size),
            nn.Sigmoid())
        self.fc = nn.Linear(hidden_size, vocabulary_size)
        self.sf = nn.Softmax()

    def forward(self, x):
        out = self.emb(x)
        out = out.view(out.size(0), -1)
        out = self.layer1(out)
        out = self.layer2(out)
        out = self.layer3(out)
        out = self.fc(out)
        out = self.sf(out)
        return out


def device(device_str):
    return int(device_str[-1])


def train(model, train_loader, test_loader):

    # Hyper Parameters
    num_epochs = args.num_epochs

    # Loss and Optimizer
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01, momentum=0.9, weight_decay=1e-4)

    criterion = nn.CrossEntropyLoss()
    train_loss = 0
    best_loss = np.inf
    start_epoch = 0

    # optionally resume from a checkpoint
    if args.resume:
        if os.path.isfile(args.resume):
            print("=> loading checkpoint '%s'" % args.resume)
            checkpoint = torch.load(args.resume)
            start_epoch = checkpoint['epoch']
            best_loss = checkpoint['best_loss']
            model.load_state_dict(checkpoint['state_dict'])
            optimizer.load_state_dict(checkpoint['optimizer'])
            print("=> loaded checkpoint '%s' (epoch %s)" %
                  (args.resume, checkpoint['epoch'] + 1))
        else:
            print("=> no checkpoint found at '%s'" % args.resume)

    for epoch in range(start_epoch, num_epochs):

        for step, (batch_inputs, batch_labels) in enumerate(train_loader):

            inp = torch.from_numpy(batch_inputs).long()
            lbl = torch.from_numpy(batch_labels).long().view(-1)

            if use_cuda:
                with torch.cuda.device(device(args.device)):
                    inp.cuda()
                    lbl.cuda()
                    model.cuda()

            x = Variable(inp)
            y = Variable(lbl)

            model.train()
            optimizer.zero_grad()
            y_hat = model(x)

            loss = criterion(y_hat, y)

            train_loss += loss.data[0]
            loss.backward()
            optimizer.step()

            # loss = p_pos.log() + p_neg.log()

            if (step + 1) % args.steps_per_stats == 0:
                test_loss = 0
                f1 = 0
                for i, (batch_inputs, batch_labels) in enumerate(test_loader):

                    inp = torch.from_numpy(batch_inputs).long()
                    lbl = torch.from_numpy(batch_labels).long().view(-1)

                    if use_cuda:
                        with torch.cuda.device(device(args.device)):
                            inp.cuda()
                            lbl.cuda()
                            model.cuda()

                    x = Variable(inp)
                    y = Variable(lbl)
                    y_hat = model(x)

                    pred = y_hat.data.numpy().argmax(axis=1)
                    f1 += f1_score(y.data.numpy(), pred, average='micro')
                    loss = criterion(y_hat, y)
                    test_loss += loss.data[0]

                print('Epoch [%d/%d], Train Loss: %.5f, Test Loss: %.5f, Test F1: %.2f'
                      % (epoch + 1, num_epochs, train_loss / args.steps_per_stats, test_loss / i, f1 / i))
                train_loss = 0

                # remember best prec@1 and save checkpoint
                is_best = best_loss > test_loss
                best_loss = min(best_loss, test_loss)
                save_checkpoint({
                    'epoch': epoch,
                    'state_dict': model.state_dict(),
                    'best_loss': best_loss,
                    'optimizer': optimizer.state_dict(),
                }, is_best)


def save_checkpoint(state, is_best):
    filename_late = "%s/predict_%s_latest.tar" % (ckptpath, arch)
    filename_best = "%s/predict_%s_best.tar" % (ckptpath, arch)
    torch.save(state, filename_late)
    if is_best:
        copyfile(filename_late, filename_best)


def add_arguments(parser):
    parser.add_argument("-w", "--win_size", type=int, required=True,
                        help="Give the length of the context window.")
    parser.add_argument("-d", "--emb_dim", type=int, required=True,
                        help="Give the dimension of the embedding vector.")
    parser.add_argument("-b", "--batch_size", type=int, default=32,
                        help="Give the size of bach to use when training.")
    parser.add_argument("-e", "--num_epochs", type=int, default=10,
                        help="Give the number of epochs to use when training.")
    parser.add_argument("--mongo_url", type=str, default='mongodb://localhost:27017/',
                        help="Supply the URL of MongoDB")
    parser.add_argument("-a", "--arch", type=str, choices=['mlp', 'cnn'],
                        default="mlp", help="Choose what type of model to use.")
    parser.add_argument("-o", "--out_dir", type=str, required=False,
                        default=gettempdir(), help="Specify the output directory.")
    parser.add_argument("-v", '--verbose', action='store_true', default=False,
                        help="Run in verbose mode.")
    parser.add_argument('-r', '--resume', default='', type=str, metavar='PATH',
                        help='path to latest checkpoint (default: none)')
    parser.add_argument("--steps_per_stats", type=int, default=1000,
                        help="How many training steps to do per stats logging, save.")
    parser.add_argument("--size_train", type=int, default=50000,
                        help="The number of sequences sampled to create the test set.")
    parser.add_argument("--size_test", type=int, default=1000,
                        help="The number of sequences sampled to create the train set.")
    parser.add_argument("--device", type=str, default='cpu',
                        help="Specify what device you'd like to use e.g. 'cpu', 'gpu0' etc.")


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    args = parser.parse_args()

    client = MongoClient(args.mongo_url)
    db = client['prot2vec']
    collection_train = db['uniprot']
    collection_test = db['sprot']
    W2V.collection_train = collection_train
    W2V.collection_test = collection_test

    W2V.size_train = args.size_train
    W2V.size_test = args.size_test

    arch = args.arch

    use_cuda = args.device != 'cpu'

    ckptpath = args.out_dir
    if not os.path.exists(ckptpath):
        os.makedirs(ckptpath)

    if arch == 'mlp':
        model = MLP(args.emb_dim, args.win_size, 512)
        train_loader = WindowBatchLoader(args.win_size, args.batch_size)
        test_loader = WindowBatchLoader(args.win_size, args.batch_size, False)
        train(model, train_loader, test_loader)

    elif arch == 'cnn':
        model = CNN(args.emb_dim, args.win_size, 256)
        train_loader = WindowBatchLoader(args.win_size, args.batch_size)
        test_loader = WindowBatchLoader(args.win_size, args.batch_size, False)
        train(model, train_loader, test_loader)

    else:
        print("Unknown model")
        exit(1)