#! /usr/bin/env python3

###############
# mim-tRNAseq #
####################################
# Main backbone and wrapper script #
####################################
# 
# author: Drew Behrens
# contact: aberens@biochem.mpg.de
# github: https://github.com/drewjbeh

import tRNAtools
import tRNAmap
import getCoverage
import sys, os, subprocess
import argparse
from pyfiglet import figlet_format

## Method for restricting cluster_id argument to float between 0 and 1
def restrictedFloat(x):
	try:
		x = float(x)
		if x < 0.0 or x > 1.0:
			raise argparse.ArgumentTypeError('{} not in range 0.0 - 1.0'.format(x))
		return x
	except ValueError:
		raise argparse.ArgumentTypeError('{} not a real number'.format(x))

def mimseq(trnas, trnaout, modomics, name, out, cluster, cluster_id, posttrans, threads, max_multi, snp_tolerance, keep_temp, mode, sample_data):
	
	# Integrity check for output folder argument...
	try:
		os.mkdir(out)
	except FileExistsError:
		raise FileExistsError("Output folder already exists!")

	if not out.endswith("/"):
		out = out + "/"


	########
	# main #
	########

	# Parse tRNA and modifications, generate SNP index
	modifications = os.path.dirname(os.path.realpath(__file__))
	modifications += "/modifications"
	coverage_bed = tRNAtools.modsToSNPIndex(trnas, trnaout, modomics, modifications, name, out, cluster, cluster_id, posttrans)

	# Generate GSNAP indeces
	genome_index_path, genome_index_name, snp_index_path, snp_index_name = tRNAtools.generateGSNAPIndeces(name, out, cluster)

	# Align
	bams_list, coverageData = tRNAmap.mainAlign(sample_data, name, genome_index_path, genome_index_name, \
		snp_index_path, snp_index_name, out, threads, snp_tolerance, keep_temp)

	# Coverage and plots
	getCoverage.getCoverage(coverage_bed, coverageData, out, max_multi)
	getCoverage.plotCoverage(out)

	# featureCounts
	tRNAmap.countReads(bams_list, mode, threads, out)

	# DESeq2
	script_path = os.path.dirname(os.path.realpath(__file__))
	sample_data = os.path.abspath(coverageData)
	deseq_cmd = "Rscript " + script_path + "/deseq.R " + out + " " + sample_data
	subprocess.call(deseq_cmd, shell=True)

	# tidy files
	tRNAtools.tidyFiles(out)

################### 
# Parse arguments #
################### 

if __name__ == '__main__':
	parser = argparse.ArgumentParser(description = 'Custom high-throughput tRNA sequencing alignment and quantification pipeline\
		based on modifications and misincorporation.', add_help = True, usage = "%(prog)s [options] sample_data")

	inputs = parser.add_argument_group("Input files")
	inputs.add_argument('-t', '--trnas', metavar='genomic tRNAs', required = True, dest = 'trnas', help = \
		'Genomic tRNA fasta file, e.g. from gtRNAdb or tRNAscan-SE. REQUIRED')
	inputs.add_argument('-o', '--trnaout', metavar = 'tRNA out file', required = True, dest = 'trnaout', help = \
		'tRNA.out file generated by tRNAscan-SE (also may be available on gtRNAdb). Contains information about tRNA features, including introns. REQUIRED')
	inputs.add_argument('-m', '--modomics', metavar = 'modomics fasta file', required = True, dest = 'modomics', help = \
		'Modomics fasta-like file with information about modified nucleotides for a species, summarised by isodecoder families. REQUIRED')

	options = parser.add_argument_group("Program options")
	options.add_argument('--cluster', required = False, dest = 'cluster', action = 'store_true',\
		help = 'Enable usearch sequence clustering of tRNAs by isodecoder - drastically reduces multi-mapping reads.')
	options.add_argument('--cluster_id', metavar = 'clutering id cutoff', dest = 'cluster_id', type = restrictedFloat, nargs = '?', default = 0.95,\
		required = False, help = 'Identity cutoff for usearch clustering between 0 and 1. Default is 0.95.')
	options.add_argument('--snp_tolerance', required = False, dest = 'snp_tolerance', action = 'store_true',\
		help = 'Enable GSNAP SNP-tolerant read alignment, where modifications effecting misincorporation are mapped as SNPs.')
	options.add_argument('--threads', metavar = 'thread number', required = False, dest = 'threads', type = int, \
		help = 'Set processor threads to use during read alignment and read counting.')
	options.add_argument('--posttrans_mod_off', required = False, dest = 'posttrans', action = 'store_true', \
		help = "Disable post-transcriptional modification of tRNAs, i.e. addition of 3'-CCA and 5'-G (His) to mature sequences. Disable for certain \
		prokaryotes (e.g. E. coli) where this is genomically encoded. Leave enabled (default) for all eukaryotes.")

	outputs = parser.add_argument_group("Output options")
	outputs.add_argument('-n', '--name', metavar = 'experiment name', required = True, dest = 'name', help = \
		'Name of experiment. Note, output files and indeces will have this as a prefix. REQUIRED')
	outputs.add_argument('--out_dir', metavar = 'output directory', required = False, dest = 'out', help = \
		'Output directory. Default is current directory. Cannot be an exisiting directory.')
	outputs.add_argument('--keep-temp', required = False, dest='keep_temp', action = 'store_true', help = \
		'Keeps multi-mapping and unmapped bam files from GSNAP alignments. Default is false.')

	featurecounts = parser.add_argument_group("featureCounts options")
	featurecounts.add_argument('--count_mode', metavar = 'featureCounts mode', required = False, dest = 'mode', help = \
		"featureCounts mode to handle reads overlapping more than one feature. Choose from 'none' (multi-overlapping reads are not counted)\
		,'all' (reads are assigned and counted for all overlapping features), or 'fraction' (each overlapping feature receives a fractional count\
		of 1/y, where y is the number of features overlapping with the read). Default is 'none'",\
	 	choices = ['none','all','fraction'])

	bedtools = parser.add_argument_group("Bedtools coverage options")
	bedtools.add_argument('--max_multi', metavar = 'Bedtools coverage multhreading', required = False, dest = 'max_multi', type = int, \
		help = 'Maximum number of bam files to run bedtools coverage on simultaneously. Increasing this number reduces processing time\
		by increasing number of files processed simultaneously. However, depending on the size of the bam files to process and\
		available memory, too many files processed at once can cause termination of mim-tRNAseq due to insufficient memory. If\
		mim-tRNAseq fails during coverage calculation, lower this number. Increase at your own discretion. Default is 3.')

	parser.add_argument('sample_data', help = 'Sample data sheet in text format, tab-separated. Column 1: full path to fastq (or fastq.gz). Column 2: condition/group.')

	parser.set_defaults(threads=1, out="./", mode = 'none', max_multi = 3)

	if len(sys.argv[1:]) == 0:
		print(figlet_format('mim-tRNAseq', font='standard'))
		print("     Modification-induced misincorporation sequencing of tRNAs\n")
		parser.print_help()
		parser.exit()
	if len(sys.argv) <= 1:
		print(figlet_format('mim-tRNAseq', font='standard'))
		print("     Modification-induced misincorporation sequencing of tRNAs\n")
		parser.print_usage()
		sys.exit(1)
	else:
		print(figlet_format('mim-tRNAseq', font='standard'))
		print("     Modification-induced misincorporation sequencing of tRNAs\n")
		args = parser.parse_args()
		mimseq(args.trnas, args.trnaout, args.modomics, args.name, args.out, args.cluster, args.cluster_id, \
			args.posttrans, args.threads, args.max_multi, args.snp_tolerance, args.keep_temp, args.mode, args.sample_data)
