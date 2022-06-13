from typing import Optional, Dict

from fab.types_ import LogProbFunc

import torch
import torch.nn as nn
from fab.target_distributions.base import TargetDistribution
from fab.utils.training import DatasetIterator
from fab.sampling_methods import AnnealedImportanceSampler, HamiltoneanMonteCarlo
from fab.wrappers.torch import WrappedTorchDist

class Energy(torch.nn.Module):
    """
    https://zenodo.org/record/3242635#.YNna8uhKjIW
    """
    def __init__(self, dim):
        super().__init__()
        self._dim = dim

    def _energy(self, x):
        raise NotImplementedError()

    def energy(self, x, temperature=None):
        assert x.shape[-1] == self._dim, "`x` does not match `dim`"
        if temperature is None:
            temperature = 1.
        return self._energy(x) / temperature

    def force(self, x, temperature=None):
        x = x.requires_grad_(True)
        e = self.energy(x, temperature=temperature)
        return -torch.autograd.grad(e.sum(), x)[0]


class DoubleWellEnergy(Energy, nn.Module):
    def __init__(self, dim, a=0.0, b=-4., c=1.):
        super().__init__(dim)
        self._a = a
        self._b = b
        self._c = c

    def _energy(self, x):
        d = x[:, [0]]
        v = x[:, 1:]
        e1 = self._a * d + self._b * d.pow(2) + self._c * d.pow(4)
        e2 = 0.5 * v.pow(2).sum(dim=-1, keepdim=True)
        return e1 + e2

    def log_prob(self, x):
        return torch.squeeze(-self.energy(x))



class ManyWellEnergy(DoubleWellEnergy, TargetDistribution):
    """Many Well target distribution create by repeating the Double Well Boltzmann distribution."""
    def __init__(self, dim=4, use_gpu: bool = True,
                 n_intermediate_distributions=1000,
                 ais_test_set_size=500,
                 *args, **kwargs):
        assert dim % 2 == 0
        self.n_wells = dim // 2
        super(ManyWellEnergy, self).__init__(dim=2, *args, **kwargs)
        self.dim = dim
        self.centre = 1.7
        self.max_dim_for_all_modes = 40  # otherwise we get memory issues on huuuuge test set
        if self.dim < self.max_dim_for_all_modes:
            dim_1_vals_grid = torch.meshgrid([torch.tensor([-self.centre, self.centre])for _ in
                                              range(self.n_wells)])
            dim_1_vals = torch.stack([torch.flatten(dim) for dim in dim_1_vals_grid], dim=-1)
            n_modes = 2**self.n_wells
            assert n_modes == dim_1_vals.shape[0]
            test_set = torch.zeros((n_modes, dim))
            test_set[:, torch.arange(dim) % 2 == 0] = dim_1_vals
            self.register_buffer("_test_set", test_set)
        else:
            print("using test set containing not all modes to prevent memory issues")

        self.shallow_well_bounds = [-1.75, -1.65]
        self.deep_well_bounds = [1.7, 1.8]

        # create test set of points generated by ais
        x, log_w = self.create_2d_test_set_with_ais(n_intermediate_distributions,
                                                    ais_test_set_size)
        self.register_buffer("ais_x", x)
        self.register_buffer("ais_log_w", log_w)

        if use_gpu:
            if torch.cuda.is_available():
                self.cuda()
                self.device = "cuda"
            else:
                self.device = "cpu"
        else:
            self.device = "cpu"


    def create_2d_test_set_with_ais(self, n_itermediate_distributions, test_set_size):
        transition_operator = HamiltoneanMonteCarlo(n_itermediate_distributions, 2)
        ais = AnnealedImportanceSampler(
            base_distribution= WrappedTorchDist(torch.distributions.MultivariateNormal(
                loc=torch.zeros(2,),
                scale_tril=torch.eye(2)*5)),
            target_log_prob=self.log_prob_2D,
            transition_operator=transition_operator,
            n_intermediate_distributions=n_itermediate_distributions,
            distribution_spacing_type="linear")
        samples, log_w = ais.sample_and_log_weights(test_set_size)
        return samples, log_w


    def get_ais_based_test_set_samples(self, batch_size: int):
        sample_probs = torch.exp(self.ais_log_w - torch.max(self.ais_log_w))
        indices = torch.multinomial(sample_probs, num_samples=int(batch_size*self.dim/2),
                                    replacement=True)
        x = self.ais_x[indices]
        x = x.reshape(batch_size, self.dim)
        return x


    def get_modes_test_set_iterator(self, batch_size: int):
        """Test set created from points manually placed near each mode."""
        if self.dim < self.max_dim_for_all_modes:
            test_set = self._test_set
        else:
            outer_batch_size = int(1e4)
            test_set = torch.zeros((outer_batch_size, self.dim))
            test_set[:, torch.arange(self.dim) % 2 == 0] = \
                -self.centre + self.centre * 2 * \
                torch.randint(high=2, size=(outer_batch_size, int(self.dim/2)))
        return DatasetIterator(batch_size=batch_size, dataset=test_set,
                               device=self.device)

    def log_prob(self, x):
        return torch.sum(
            torch.stack(
                [super(ManyWellEnergy, self).log_prob(x[:, i*2:i*2+2])
                 for i in range(self.n_wells)]),
            dim=0)

    def log_prob_2D(self, x):
        # for plotting, given 2D x
        return super(ManyWellEnergy, self).log_prob(x)

    def performance_metrics(self, samples: torch.Tensor, log_w: torch.Tensor,
                            log_q_fn: Optional[LogProbFunc] = None,
                            batch_size: Optional[int] = None) -> Dict:
        if log_q_fn is None:
            return {}
        else:
            del samples
            del log_w
            sum_log_prob = 0.0
            test_set_iterator = self.get_modes_test_set_iterator(batch_size=batch_size)
            for x in test_set_iterator:
                sum_log_prob += torch.sum(log_q_fn(x)).item()
            with torch.no_grad():
                test_set_from_ais = self.get_ais_based_test_set_samples(batch_size)
                test_set_ais_samples_mean_log_prob = \
                    torch.mean(log_q_fn(test_set_from_ais)).item()
            info = {
                "test_set_modes_mean_log_prob": sum_log_prob / test_set_iterator.test_set_n_points,
                "test_set_ais_mean_log_prob": test_set_ais_samples_mean_log_prob
            }
            return info