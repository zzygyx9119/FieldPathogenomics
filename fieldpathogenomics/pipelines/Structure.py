import os
import sys
import json
import multiprocessing_on_dill as multiprocessing

import fieldpathogenomics
from fieldpathogenomics.pipelines.Callset import HD5s
import fieldpathogenomics.utils as utils

from bioluigi.slurm import SlurmExecutableTask, SlurmTask
from bioluigi.utils import CheckTargetNonEmpty
from bioluigi.decorators import requires

import luigi
from luigi import LocalTarget

FILE_HASH = utils.file_hash(__file__)
PIPELINE = os.path.basename(__file__).split('.')[0]
VERSION = fieldpathogenomics.__version__.rsplit('.', 1)[0]


@requires(HD5s)
class PrepStructureInput(SlurmTask, CheckTargetNonEmpty):
    '''Takes the HD5 file containing high quality biallelic synonymous
       sites generated by fielpathogenomics.Callset.GetSyn and converts
       it into a matrix of integer encoded pseudohaplotypes for structure.

       Also calculates linkage between sites and selects sites that have r^2 < max_linkage
       '''

    max_linkage = luigi.FloatParameter(default=0.1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 8000
        self.n_cpu = 1
        self.partition = "nbi-short"

    def output(self):
        return LocalTarget(os.path.join(self.base_dir, VERSION, PIPELINE, self.output_prefix, self.output_prefix + ".str"))

    def work(self):

        import numpy as np
        import allel
        import h5py
        import pandas as pd
        from luigi.file import atomic_file

        # Opens the SynSNPS file, which contains only biallelic synonymous sites
        callset = h5py.File(self.input()['syn'].path, mode='r')
        genotypes = allel.GenotypeChunkedArray(callset['calldata']['genotype'])
        samples = np.array([x.decode() for x in callset['samples']])

        # Selects site with r**2 linkage < max_linkage
        n_ref = genotypes.to_n_ref(fill=-9)
        unlinked = allel.locate_unlinked(n_ref, threshold=self.max_linkage)[:]

        # Create pseudohaplotypes (0=ref, 1=alt, -1=missing)
        hap_matrix = genotypes[:][unlinked].to_haplotypes()

        # Double up the sample names
        samples_dup = np.array(list(zip(samples, samples))).reshape(-1, 1)

        hap_df = pd.DataFrame(np.hstack((samples_dup, hap_matrix.T)))

        # Atomic write TSV file output
        af = atomic_file(self.output().path)
        hap_df.to_csv(af.tmp_path, sep='\t', index=False)
        af.move_to_final_destination()


@requires(PrepStructureInput)
class STRUCTURE(SlurmExecutableTask, CheckTargetNonEmpty):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 4000
        self.n_cpu = 10
        self.partition = "nbi-long"

    def output(self):
        return LocalTarget(os.path.join(self.base_dir, VERSION, PIPELINE, self.output_prefix, "WHATEVER STRUCTURE OUTPUTS"))

    def work_script(self):
        return '''#!/bin/bash
               source STRUCTURE-XXXXXXX
               cd {output_dir}
               set -euo pipefail

               ## FILL IN THE ACTUAL COMMAND HERE
               SUTRUCTURE -T {n_cpu} {input} {output}.temp

               mv {output}.temp {output}
               '''.format(output_dir=os.path.dirname(self.output().path),
                          n_cpu=self.n_cpu,
                          input=self.input().path,
                          output=self.output)


# -----------------------------------------------------------------------------------

if __name__ == '__main__':
    multiprocessing.set_start_method('forkserver')
    logger, alloc_log = utils.logging_init(log_dir=os.path.join(os.getcwd(), 'logs'),
                                           pipeline_name=os.path.basename(__file__))

    with open(sys.argv[1], 'r') as libs_file:
        lib_list = [line.rstrip() for line in libs_file]

    name = os.path.split(sys.argv[1])[1].split('.', 1)[0]

    luigi.run(['STRUCTURE', '--output-prefix', name,
                            '--lib-list', json.dumps(lib_list),
                            '--star-genome', os.path.join(utils.reference_dir, 'genome'),
                            '--reference', os.path.join(utils.reference_dir, 'PST130_contigs.fasta'),
                            '--mask', os.path.join(utils.reference_dir, 'PST130_RNASeq_collapsed_exons.bed')] + sys.argv[3:])
