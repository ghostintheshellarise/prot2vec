#!/usr/bin/env bash

GPU_FLAG=''

while getopts ":g:" opt; do
  case $opt in
    g)
    ;;
    \?)
      echo "Invalid option: -$OPTARG" >&2
      exit 1
      ;;
    :)
      echo "-gpu was triggered."
      GPU_FLAG='-gpu'
      ;;
  esac
done


conda create -p virtualenv python=3.6

conda install -p virtualenv pip -c anaconda

conda install -p virtualenv numpy
conda install -p virtualenv scikit-learn
conda install -p virtualenv matplotlib


if [ -z $GPU_FLAG ] ; then
    conda install -p virtualenv pytorch torchvision cuda80 -c soumith
else
    conda install -p virtualenv pytorch torchvision -c soumith
fi

conda install -p virtualenv visdom -c conda-forge

conda install -p virtualenv --upgrade tensorflow$GPU_FLAG
conda install -p virtualenv install tf-nightly$GPU_FLAG
conda install -p virtualenv  tensorboard

conda install -p virtualenv keras -c conda-forge


### activate virtualenv
source activate virtualenv

pip install wget    # Does NOT install properly by conda

pip install pandas

pip install biopython

pip install gensim

pip install pymongo

pip install tqdm

### install virtualenv as kernel to ipython notebook
pip install ipykernel
python -m ipykernel install --user --name virtualenv

git clone https://www.github.com/datalogai/recurrentshop.git
python recurrentshop/setup.py install






