PY ?= python

.PHONY: all preprocess train kelly backtest test clean

all: preprocess train kelly backtest test

preprocess:
	$(PY) src/data/preprocess.py --input raw_data/ --output processed/

train:
	$(PY) src/models/train_lgbm.py --data processed/train.csv --model_dir artifacts/

kelly:
	$(PY) src/betting/kelly_calculator.py --prob 0.15 --odds 10.0 --alpha 0.1

backtest:
	$(PY) src/backtest/simulator.py --start_date 2022-01-01 --end_date 2023-12-31

test:
	$(PY) -m pytest tests/ -q

clean:
	rm -rf processed/*.csv artifacts/*.txt artifacts/*.pkl artifacts/*.json backtest_results/

sweep:
	$(PY) src/backtest/sweep.py --start_date 2021-01-01 --end_date 2023-12-31 --holdout_start 2023-04-01

dashboard:
	$(PY) src/web/export_dashboard.py --output web/data.json
	$(PY) src/web/build_static.py --output web/dist/index.html
