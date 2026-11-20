"""Import exported human-reviewed JSON and compute a bounded, truthful score."""
import argparse
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from handwriting.acceptance import import_and_score

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('review', type=Path)
    parser.add_argument('--results', type=Path, default=ROOT / 'data/evaluations/stage012_20261106/results.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/evaluations/stage012_20261106/人工确认准确率.json')
    args = parser.parse_args()
    result = import_and_score(args.review, args.results, ROOT / 'data/手写素材', args.output)
    print(result['status'], 'images:', len(result['images']))
