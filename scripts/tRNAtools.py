#!/usr/bin/env python3

##################################################################################
# Utilities for tRNA modification parsing, transcript building, and SNP indexing #
##################################################################################

from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from Bio import Alphabet
from Bio.Blast.Applications import NcbiblastnCommandline
from Bio.Blast import NCBIXML
import re, copy, sys, os, shutil, subprocess
from pathlib import Path


def tRNAparser (gtRNAdb, tRNAscan_out, modomics, modifications_table, posttrans_mod_off):
# tRNA sequence files parser and dictionary building

	# Generate modification reference table
	modifications = modificationParser(modifications_table)
	temp_name = gtRNAdb.split("/")[-1]
                
	print("\n\n+" + ("-" * (len(temp_name)+24)) + "+\
		\n| Starting analysis for {} |\
		\n+".format(temp_name) + ("-" * (len(temp_name)+24)) + "+\n")
	      
	print("Processing tRNA sequences...")

	# Build dictionary of sequences from gtRNAdb fasta
	tRNA_dict = {}
	temp_dict = SeqIO.to_dict(SeqIO.parse(gtRNAdb,"fasta"))

	# Initialise intron dictionary
	Intron_dict = initIntronDict(tRNAscan_out)

	for seq in temp_dict:
		tRNA_dict[seq] = {}
		tRNAseq = intronRemover(Intron_dict, temp_dict, seq, posttrans_mod_off)
		tRNA_dict[seq]['sequence'] = tRNAseq

		if 'nmt' in seq:
			tRNA_dict[seq]['type'] = 'mitochondrial'
			new_seq = str(seq.split('-')[0]) + '_' + '-'.join(seq.split('-')[1:])
			tRNA_dict[new_seq] = tRNA_dict[seq]
			del tRNA_dict[seq]
		else:
			tRNA_dict[seq]['type'] = 'cytosolic'


	# Read in and parse modomics file to contain similar headers to tRNA_dict
	# Save in new dict

	print("Processing modomics database...\n")
	modomics_file = open(modomics, 'r')
	modomics_dict = {}
	
	for line in modomics_file:
		line = line.strip()
		sameIDcount = 0
		if line.startswith('>'):
			line = line.replace(' | ','|')
			# Replace modomics antidon with normal ACGT codon
			anticodon = str(line.split('|')[2])
			new_anticodon = getUnmodSeq(anticodon, modifications)

			# Check amino acid name in modomics - set to iMet if equal to Ini to match gtRNAdb
			amino = str(line.split('|')[1])
			if amino == 'Ini' :
				amino = 'iMet'

			curr_id = str(line.split('|')[3].split(' ')[0]) + '_' + str(line.split('|')[3].split(' ')[1]) + '_' + str(line.split('|')[0].split(' ')[1]) + '-' + amino + '-' + new_anticodon
		
			#Unique names for duplicates
			if curr_id in modomics_dict:
				sameIDcount += 1
				curr_id = curr_id + '-' + str(sameIDcount)

			tRNA_type = str(line.split('|')[4])
			modomics_dict[curr_id] = {'sequence':'','type':tRNA_type, 'anticodon':new_anticodon}
		
		else:
			sequence = line.strip().replace('U','T').replace('-','')
			modomics_dict[curr_id]['sequence'] = sequence
			unmod_sequence = getUnmodSeq(sequence, modifications)

			# Return list of modified nucl indices and add to modomics_dict
			# add unmodified seq to modomics_dict by lookup to modifications
			nonMod = ['A','C','G','T','-']
			modPos = [i for i, x in enumerate(modomics_dict[curr_id]['sequence']) if x not in nonMod]
			modomics_dict[curr_id]['modified'] = modPos
			modomics_dict[curr_id]['unmod_sequence'] = unmod_sequence

			# If 'N' in anticodon then duplicate entry 4 times for each possibility
			anticodon = curr_id.split('-')[2]
			if 'N' in anticodon:
				for rep in ['A','C','G','T']:
					duplicate_item = str(curr_id.split('-')[0]) + '-' + str(curr_id.split('-')[1]) + '-' + str(anticodon.replace('N', rep))
					duplicate_unmod_seq = modomics_dict[curr_id]['unmod_sequence'].replace('N',rep)
					modomics_dict[duplicate_item] = copy.deepcopy(modomics_dict[curr_id])
					modomics_dict[duplicate_item]['unmod_sequence'] = duplicate_unmod_seq
				del modomics_dict[curr_id]
			else:
				duplicate_item = curr_id

	modomics_file.close()

	return(tRNA_dict,modomics_dict)

def modsToSNPIndex(gtRNAdb, tRNAscan_out, modomics, modifications_table, experiment_name, out_dir, cluster = False, cluster_id = 0.95, posttrans_mod_off = False):
# Builds SNP index needed for GSNAP based on modificaiton data fro each tRNA

	nomatch_count = 0
	match_count = 0
	total_snps = 0
	snp_records = list()
	seq_records = list()
	anticodon_list = list()
	tRNAbed = open(out_dir + experiment_name + "_maturetRNA.bed","w")
	# generate modomics_dict and tRNA_dict
	tRNA_dict, modomics_dict = tRNAparser(gtRNAdb, tRNAscan_out, modomics, modifications_table, posttrans_mod_off)
	temp_dir = out_dir + "/tmp/"

	try:
		os.mkdir(temp_dir)
	except FileExistsError:
		print("Temp folder present - previous run interrupted? Overwriting old temp files...\n")

	###################################################
	## Main code for matching and SNP index building ##
	###################################################

	# match each sequence in tRNA_dict to value in modomics_dict using BLAST

	print("+------------------------+ \
		\n| Beginning SNP indexing |\
		\n+------------------------+ \n")	

	for seq in tRNA_dict:
		# Initialise list of modified sites for each tRNA
		tRNA_dict[seq]['modified'] = []
		temp_tRNAFasta = open(temp_dir + seq + ".fa","w")
		temp_tRNAFasta.write(">" + seq + "\n" + tRNA_dict[seq]['sequence'] + "\n")
		temp_tRNAFasta.close()
		tRNA_dict[seq]['anticodon'] = anticodon = seq.split('-')[2]
		if not anticodon in anticodon_list:
			anticodon_list.append(anticodon)
		match = {k:v for k,v in modomics_dict.items() if anticodon in v['anticodon']}
		if len(match) >= 1:
			temp_matchFasta = open(temp_dir + "modomicsMatch.fasta","w")
			for i in match:	
				temp_matchFasta.write(">" + i + "\n" + match[i]['unmod_sequence'] + "\n")
			temp_matchFasta.close()

			#blast
			blastn_cline = NcbiblastnCommandline(query = temp_tRNAFasta.name, subject = temp_matchFasta.name, task = 'blastn-short', out = temp_dir + "blast_temp.xml", outfmt = 5)
			blastn_cline()

			#parse XML result and store hit with highest bitscore	
			blast_record = NCBIXML.read(open(temp_dir + "blast_temp.xml","r"))
			maxbit = 0
			tophit = ''
			for alignment in blast_record.alignments:
				for hsp in alignment.hsps:
					if (hsp.bits > maxbit) and (hsp.align_length / alignment.length >= 0.9):
						maxbit = hsp.bits
						tophit = alignment.title.split(' ')[0]
			
			# return list of all modified positions for the match as long as there is only 1
			# index SNPs
			# Format for SNP index (space separated):
			# >snpID chromosomeName:position(1-based) RefMod
			# e.g. >rs111 Homo_sapiens_nmt_tRNA-Leu-TAA-1-1_exp0:29 GN

			if tophit:
				match_count += 1
				tRNA_dict[seq]['modified'] = match[tophit]['modified']
				for (index, pos) in enumerate(tRNA_dict[seq]['modified']):
					# Build snp_records with chromosomes equal to sequence name since this will be appended to the genome as individual chr
					# Position is 1-based for iit_store and snpindex GSNAP routines plus 20 "N" nucleotides, i.e. pos + 21
					snp_records.append(">" + seq + "_snp" + str(index) + " " + seq + ":" + str(pos + 21) + " " + tRNA_dict[seq]['sequence'][pos].upper() + "N")
			elif len(tophit) == 0:
				nomatch_count += 1
		if len(match) == 0:
			nomatch_count += 1
		total_snps+= len(tRNA_dict[seq]['modified'])

		# Build seqrecord list for writing - add 20 N's up and down of transcript sequence
		seq_records.append(SeqRecord(Seq(("N" * 20 ) + tRNA_dict[seq]['sequence'].upper() + ("N" * 20), Alphabet.generic_dna), id = str(seq)))

		tRNAbed.write(seq + "\t20\t" + str(len(tRNA_dict[seq]['sequence']) + 20) + "\t" + seq + "\t1000\t+\n" )

	tRNAbed.close()

	print("{} total tRNA gene sequences".format(len(tRNA_dict)))
	print("{} sequences with a match to Modomics dataset\n".format(match_count))

	# if clustering is not activated then write ful gff report on total SNPs written 
	if not cluster:
		coverage_bed = tRNAbed.name
		with open(out_dir + experiment_name + "_tRNA.gff","w") as tRNAgff:	
			for seq in tRNA_dict:
				tRNAgff.write(seq + "\ttRNAseq\texon\t21\t" + str(len(tRNA_dict[seq]['sequence']) + 20) + "\t.\t+\t0\tgene_id '" + seq + "'\n")
		print("{:,} modifications written to SNP index\n".format(total_snps))

	##########################
	# Cluster tRNA sequences #
	##########################

	elif cluster:

		print("**** Clustering tRNA sequences ****")
		print("Clustering tRNA sequences by {:.0%} similarity...".format(cluster_id))
		# get dictionary of sequences for each anticodon and write to fastas
		for anticodon in anticodon_list:
			seq_set = {k:{'sequence':v['sequence'],'modified':v['modified']} for k,v in tRNA_dict.items() if v['anticodon'] == anticodon}
			with open(temp_dir + anticodon + "_allseqs.fa","w") as anticodon_seqs:
				for sequence in seq_set:
					anticodon_seqs.write(">" + sequence + "\n" + seq_set[sequence]['sequence'] + "\n")
			# run usearch on each anticodon sequence fatsa to cluster
			cluster_cmd = "usearch -cluster_fast " + temp_dir + anticodon + "_allseqs.fa -id " + str(cluster_id) + " -centroids " + temp_dir + anticodon + "_centroids.fa -uc " + temp_dir + anticodon + "_clusters.uc &> /dev/null" 
			subprocess.call(cluster_cmd, shell = True)
		# combine centroids files into one file
		combine_cmd = "cat " + temp_dir + "*_centroids.fa > " + temp_dir + "all_centroids.fa"
		subprocess.call(combine_cmd, shell = True)
		# add N's
		final_centroids = [SeqRecord(Seq(("N" *20) + str(seq_record.seq).upper() + ("N" * 20), Alphabet.generic_dna), id = seq_record.id) for seq_record in SeqIO.parse(temp_dir + "all_centroids.fa", "fasta")]
			
		# read cluster files, get nonredudant set of mod positions of all members of a cluster, create snp_records for writing SNP index
		cluster_pathlist = Path(temp_dir).glob("**/*_clusters.uc")
		mod_lists = dict()
		snp_records = list()
		cluster_dict = dict()
		cluster_num = 0
		total_snps = 0
		clusterbed = open(out_dir + experiment_name + "_clusters.bed","w")
		coverage_bed = clusterbed.name
		clustergff = open(out_dir + experiment_name + "_tRNA.gff","w")
		for path in cluster_pathlist:
			with open(path,"r") as cluster_file:
				for line in cluster_file:
					line = line.strip()
					if line.split("\t")[0] == "S":
						cluster_num += 1
						cluster_name = line.split("\t")[8]
						mod_lists[cluster_name] = tRNA_dict[cluster_name]["modified"]
						clusterbed.write(cluster_name + "\t20\t" + str(len(tRNA_dict[cluster_name]['sequence']) + 20) + "\t" + cluster_name + "\t1000\t+\n" )
						clustergff.write(cluster_name + "\ttRNAseq\texon\t21\t" + str(len(tRNA_dict[cluster_name]['sequence']) + 20) + "\t.\t+\t0\tgene_id '" + cluster_name + "'\n")
						cluster_dict[cluster_name] = cluster_num
					elif line.split("\t")[0] == "H":
						member_name = line.split("\t")[8]
						cluster_name = line.split("\t")[9]
						# if member of cluster is 100% identical (i.e. "=" in 8th column of cluster file)
						if line.split("\t")[7] == "=":
							mod_lists[cluster_name] = list(set(mod_lists[cluster_name] + tRNA_dict[member_name]["modified"]))
							cluster_dict[member_name] = cluster_dict[cluster_name]
						# else if there are inserts or deletions in the member, make new cluster for this sequence, add to centroids to be written to clusterTranscripts
						elif re.search("[ID]",line.split("\t")[7]):
							cluster_num += 1
							mod_lists[member_name] = tRNA_dict[member_name]["modified"]
							final_centroids.append(SeqRecord(Seq(("N" * 20) + str(tRNA_dict[member_name]["sequence"]).upper() + ("N" * 20), Alphabet.generic_dna), id = member_name))
							clusterbed.write(member_name + "\t20\t" + str(len(tRNA_dict[member_name]['sequence']) + 20) + "\t" + member_name + "\t1000\t+\n" )
							clustergff.write(member_name + "\ttRNAseq\texon\t21\t" + str(len(tRNA_dict[member_name]['sequence']) + 20) + "\t.\t+\t0\tgene_id '" + member_name + "'\n")
							cluster_dict[member_name] = cluster_num
						# else find mismatches and build non-redundant set
						else:
							cluster_seq = tRNA_dict[cluster_name]["sequence"]
							member_seq = tRNA_dict[member_name]["sequence"]
							mismatches = [i for i in range(len(member_seq)) if member_seq[i] != cluster_seq[i]]
							member_mods = list(set(tRNA_dict[member_name]["modified"] + mismatches))
							mod_lists[cluster_name] = list(set(mod_lists[cluster_name] + member_mods))
							cluster_dict[member_name] = cluster_dict[cluster_name]
		
		clusterbed.close()

		# Write cluster information to tsv
		with open(out_dir + experiment_name + "clusterInfo.txt","w") as clusterInfo:
			for key, value in cluster_dict.items():
				clusterInfo.write("{}\t{}\n".format(key, value))

		# write cluster transcripts
		with open(str(out_dir + experiment_name + '_clusterTranscripts.fa'), "w") as clusterTranscripts:
			SeqIO.write(final_centroids, clusterTranscripts, "fasta")

		print("{} clusters created from {} tRNA sequences\n".format(cluster_num,len(tRNA_dict)))

		for cluster in mod_lists:
			total_snps += len(mod_lists[cluster])
			for (index, pos) in enumerate(mod_lists[cluster]):
				# Build snp_records as before but with cluster names and non-redundant sets of modifications
				# Position is 1-based for iit_store and snpindex GSNAP routines plus 20 "N" nucleotides, i.e. pos + 21
				snp_records.append(">" + cluster + "_snp" + str(index) + " " + cluster + ":" + str(pos + 21) + " " + tRNA_dict[cluster]['sequence'][pos].upper() + "N")
 
		print("{:,} modifications written to SNP index\n".format(total_snps))		
	
	with open(str(out_dir + experiment_name + '_tRNATranscripts.fa'), "w") as temptRNATranscripts:
		SeqIO.write(seq_records, temptRNATranscripts, "fasta")
	
	# write outputs for indexing 
	with open(out_dir + experiment_name + "_modificationSNPs.txt", "w") as snp_file:
		for item in snp_records:
			snp_file.write('{}\n'.format(item))
	
	shutil.rmtree(temp_dir)
	# Return coverage_bed (either tRNAbed or clusterbed depending on --cluster) for coverage calculation method
	return(coverage_bed)

def generateGSNAPIndeces(experiment_name, out_dir, cluster = False):
	# Builds genome and snp index files required by GSNAP

	print("+--------------------------+ \
		 \n| Generating GSNAP indeces |\
		 \n+--------------------------+ \n")

	genome_index_path = out_dir + experiment_name + "_tRNAgenome"
	genome_index_name = genome_index_path.split("/")[-1]
	
	try:
		os.mkdir(genome_index_path)
	except FileExistsError:
		print("Genome index folder found! Building indeces anyway...")
	
	if cluster:
		genome_file = out_dir + experiment_name + "_clusterTranscripts.fa"
	else:
		genome_file = out_dir + experiment_name + "_tRNATranscripts.fa"

	index_cmd = "gmap_build -D " + out_dir + " -d " + experiment_name + "_tRNAgenome " + genome_file + \
				" &> " + out_dir + "genomeindex.log"
	subprocess.call(index_cmd, shell = True) 
	print("Genome indeces done...")

	snp_index_path = out_dir + experiment_name + "snp_index"

	try:
		os.mkdir(snp_index_path)
	except FileExistsError:
		print("SNP index folder found! Building indeces anyway...\n")

	snp_file = out_dir + experiment_name + "_modificationSNPs.txt"
	snp_index_name = snp_file.split("/")[-1]. split(".txt")[0]
	index_cmd = "cat " + snp_file + " | iit_store -o " + snp_index_path + "/" + snp_index_name + " &> " + out_dir + "snpindex.log"
	subprocess.call(index_cmd, shell = True)
	index_cmd = "snpindex -D " + genome_index_path + " -d " + experiment_name + "_tRNAgenome -V " + snp_index_path + \
				" -v " + experiment_name + "_modificationSNPs " + snp_index_path + "/" + experiment_name + \
				"_modificationSNPs.iit &>> " + out_dir + "snpindex.log"
	subprocess.call(index_cmd, shell = True)
	print("SNP indeces done...\n")

	return(genome_index_path, genome_index_name, snp_index_path, snp_index_name)

def modificationParser(modifications_table):
	# Read in modifications and build dictionary

		mods = open(modifications_table, 'r')
		modifications = {}
		for line in mods:
			if not line.startswith("#"):
				name, abbr, ref, mod = line.split('\t')
				# replace unknown modifications with reference of N
				if not ref or ref.isspace():
					ref = 'N'
				if mod and not mod.isspace():
					modifications[mod.strip()] = {'name':name.strip(), 'abbr':abbr.strip(), 'ref':ref.strip()}
		return(modifications)

def getUnmodSeq (seq, modification_table):
# Change modified bases into standard ACGT in input sequence

	new_seq = []
	for char in seq:
		# for insertions ('_') make reference N - this is not described in the modifications table
		if char == '_':
			char = 'N'
		else:
			char = modification_table[char]['ref']
			# Change queuosine to G (reference is preQ0base in modification file)
			if char == 'preQ0base':
				char = 'G'

		new_seq.append(char)

	new_seq = ''.join(new_seq)
	new_seq = new_seq.replace('U','T')
	return(new_seq)

def initIntronDict(tRNAscan_out):
# Build dictionary of intron locations

	Intron_dict = {}
	tRNAscan = open(tRNAscan_out, 'r') 
	intron_count = 0
	for line in tRNAscan:
		if line.startswith("chr"):
			tRNA_ID = line.split()[0] + ".trna" + line.split()[1]
			tRNA_start = int(line.split()[2])
			intron_start = int(line.split()[6])
			intron_stop = int(line.split()[7])
			# if inton boundaries are not 0, i.e. there is an intron then add to dict
			if (intron_start > 0) & (intron_stop > 0):
				if tRNA_start > intron_start: # tRNA is on reverse strand
					intron_count += 1
					intron_start = tRNA_start - intron_start
					intron_stop = tRNA_start - intron_stop + 1 # needed for python 0 indexing and correct slicing of intron
				else: # tRNA is on forward strand
					intron_count += 1
					intron_start -= tRNA_start
					intron_stop -= tRNA_start
					intron_stop += 1 # python 0 indexing

				Intron_dict[tRNA_ID] = {}
				Intron_dict[tRNA_ID]['intron_start'] = intron_start
				Intron_dict[tRNA_ID]['intron_stop'] = intron_stop

	print("{} introns registered...".format(intron_count))
	return(Intron_dict)


def intronRemover (Intron_dict, seqIO_dict, seqIO_record, posttrans_mod_off):
# Use Intron_dict to find and remove introns plus add CCA and 5' G for His (if eukaryotic)

	# Find a match, slice intron and add G and CCA
	ID = re.search("tRNAscan-SE ID: (.*?)\).",seqIO_dict[seqIO_record].description).group(1)
	if ID in Intron_dict:
		seq = str(seqIO_dict[seqIO_record].seq[:Intron_dict[ID]['intron_start']] + seqIO_dict[seqIO_record].seq[Intron_dict[ID]['intron_stop']:])
	else:
		seq = str(seqIO_dict[seqIO_record].seq)
	if 'His' in seqIO_record and posttrans_mod_off == False:
		seq = 'G' + seq + 'CCA'
	elif posttrans_mod_off == False:
		seq = seq + 'CCA'

	return(seq)

def tidyFiles (out_dir):
	
	os.mkdir(out_dir + "annotation/")
	os.mkdir(out_dir + "align/")
	os.mkdir(out_dir + "indices/")
	os.mkdir(out_dir + "cov/")
	os.mkdir(out_dir + "counts/")

	files = os.listdir(out_dir)

	for file in files:
		full_file = out_dir + file
		if (file.endswith("bed") or file.endswith("bed") or file.endswith("gff") or file.endswith("fa") or "clusterInfo" in file or "modificationSNPs" in file):
			shutil.move(full_file, out_dir + "annotation")
		if (file.endswith("tRNAgenome") or file.endswith("index") or "index.log" in file):
			shutil.move(full_file, out_dir + "indices")
		if (file.endswith("bam") or file == "align.log" or file == "mapping_stats.txt"):
			shutil.move(full_file, out_dir + "align")
		if ("cov" in file):
			shutil.move(full_file, out_dir + "cov")
		if ("counts".upper() in file.upper()):
			shutil.move(full_file, out_dir + "counts")
