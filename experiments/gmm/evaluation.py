import hydra

from fab.target_distributions.gmm import GMM
import pandas as pd
import os
from omegaconf import DictConfig
import torch

from experiments.load_model_for_eval import load_model


PATH = os.getcwd()


def evaluate(cfg: DictConfig, path_to_model, num_samples=int(1e4)):
    """Evaluates model, sets the AIS target to p."""
    torch.set_default_dtype(torch.float32)
    torch.manual_seed(cfg.training.seed)
    target = GMM(dim=cfg.target.dim, n_mixes=cfg.target.n_mixes,
                 loc_scaling=cfg.target.loc_scaling, log_var_scaling=cfg.target.log_var_scaling,
                 use_gpu=False, n_test_set_samples=num_samples)
    if cfg.training.use_64_bit:
        torch.set_default_dtype(torch.float64)
        target = target.double()
    model = load_model(cfg, target, path_to_model)
    model.set_ais_target(min_is_target=False)
    eval = model.get_eval_info(num_samples, 500)
    return eval


# use base config of GMM but overwrite for specific model.
@hydra.main(config_path="../config", config_name="gmm.yaml")
def main(cfg: DictConfig):
    model_names = ["target_kld", "flow_nis", "flow_kld", "rsb", "snf", "fab_no_buffer", "fab_buffer"]
    seeds = [0, 1, 2]
    num_samples = int(5e4)

    results = pd.DataFrame()
    for model_name in model_names:
        print(model_name)
        if model_name and model_name[0:3] == "snf":
            # Update flow architecture for SNF if used.
            cfg.flow.use_snf = True
        else:
            cfg.flow.use_snf = False
        if model_name and model_name[0:3] == "rsb":
            cfg.flow.resampled_base = True
        else:
            cfg.flow.resampled_base = False
        for seed in seeds:
            name = model_name + f"_seed{seed}"
            path_to_model = f"{PATH}/models/{name}.pt"
            eval_info = evaluate(cfg, path_to_model, num_samples)
            eval_info.update(seed=seed,
                             model_name=model_name)
            results = results.append(eval_info, ignore_index=True)

    keys = ["eval_ess_flow", "eval_ess_ais", "test_set_mean_log_prob", 'kl_forward']
    print("\n *******  mean  ********************** \n")
    print(results.groupby("model_name").mean()[keys])
    print("\n ******* std ********************** \n")
    print(results.groupby("model_name").sem(ddof=0)[keys])
    print("overall results")
    print(results[["model_name", "seed", "eval_ess_flow", "eval_ess_ais", "test_set_mean_log_prob"]])
    results.to_csv(open(FILENAME_EVAL_INFO, "w"))


# use base config of GMM but overwrite for specific model.
@hydra.main(config_path="../config", config_name="gmm.yaml")
def alpha_study(cfg: DictConfig):
    alpha_values = ["025", "05", "1", "15", "2", "3"]
    seeds = [0]  # , 1, 2]
    num_samples = int(5e4)

    results = pd.DataFrame()
    fab_type = "no_buff"
    for alpha in alpha_values:
        for seed in seeds:
            name_without_seed = f"{fab_type}_alpha{alpha}"
            name = name_without_seed + f"_seed{seed}"
            path_to_model = f"{PATH}/models_alpha/{name}.pt"
            eval_info = evaluate(cfg, path_to_model, num_samples)
            eval_info.update(seed=seed,
                             model_name=name_without_seed)
            results = results.append(eval_info, ignore_index=True)

    keys = ["eval_ess_flow", "eval_ess_ais", "test_set_mean_log_prob", 'kl_forward']
    print("\n *******  mean  ********************** \n")
    print(results.groupby("model_name").mean()[keys])
    print("\n ******* std ********************** \n")
    print(results.groupby("model_name").sem(ddof=0)[keys])
    print("overall results")
    print(results[["model_name", "seed", "eval_ess_flow", "eval_ess_ais", "test_set_mean_log_prob"]])
    results.to_csv(open(FILENAME_EVA_ALPHA_INFO, "w"))


FILENAME_EVAL_INFO = PATH + "/gmm_results.csv"
FILENAME_EVA_ALPHA_INFO = PATH + "/gmm_alpha_results.csv"

if __name__ == '__main__':
    # main()
    alpha_study()
