#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#	Representing a ClinicalTrials.gov study
#
#	2012-12-13	Created by Pascal Pfiffner
#

import datetime
import dateutil.parser
import os
import logging
import codecs
import json
import re
import uuid

import requests
requests_log = logging.getLogger("requests.packages.urllib3")
requests_log.setLevel(logging.WARNING)

from mngobject import MNGObject
from nlp import split_inclusion_exclusion, list_to_sentences
from umls import UMLS, UMLSLookup, SNOMEDLookup, RxNormLookup
from paper import Paper
from ctakes import cTAKES
from metamap import MetaMap


class Study (MNGObject):
	""" Describes a study found on ClinicalTrials.gov.
	"""
	
	ctakes = None
	metamap = None
	
	def __init__(self, nct=0):
		super(Study, self).__init__(nct)
		self.papers = None
		
		# NLP
		self.nlp = []
		if Study.ctakes is not None:
			self.nlp.append(cTAKES(Study.ctakes))
		if Study.metamap is not None:
			self.nlp.append(MetaMap(Study.metamap))
		
		self.waiting_for_ctakes_pmc = False
	
	@property
	def nct(self):
		return self.id
	
	@property
	def criteria(self):
		return self.doc.get('criteria', []) if self.doc is not None else []
	
	@criteria.setter
	def criteria(self, criteria):
		if self.doc is None:
			self.doc = {}
		self.doc['criteria'] = criteria
	
	def date(self, dt):
		""" Returns a tuple of the string date and the parsed Date object for
		the requested JSON object. """
		dateval = None
		parsed = None
		
		if dt is not None:
			date_dict = self.doc.get(dt) if self.doc else None
			if type(date_dict) is dict:
				dateval = date_dict.get('value')
				
				# got it, parse
				if dateval:
					dateregex = re.compile('(\w+)\s+((\d+),\s+)?(\d+)')
					searched = dateregex.search(dateval)
					match = searched.groups() if searched is not None else []
					
					# convert it to almost-ISO-8601. If day is missing use 28 to not crash the parser for February
					fmt = "%s-%s-%s" % (match[3], str(match[0])[0:3], str('00' + match[2])[-2:] if match[2] else 28)
					parsed = dateutil.parser.parse(fmt)
		
		return (dateval, parsed)
	
	def json(self, extra_fields=['brief_summary']):
		""" Returns a JSON-ready representation.
		There is a standard set of fields and the fields stated in
		"extra_fields" will be appended.
		"""
		
		# best title
		title = self.doc.get('brief_title') if self.doc else None
		if not title:
			title = self.doc.get('official_title') if self.doc else 'Unknown Title'
		acronym = self.doc.get('acronym') if self.doc else None
		if acronym:
			if title:
				title = "%s: %s" % (acronym, title)
			else:
				title = acronym
		
		# criteria
		elig = self.doc.get('eligibility', {})
		c = {
			'gender': elig.get('gender'),
			'min_age': elig.get('.minimum_age'),
			'max_age': elig.get('maximum_age'),
			'healthy_volunteers': elig.get('healthy_volunteers'),
			'formatted': self.eligibility_formatted
		}
		
		# main dict
		d = {
			'nct': self.id,
			'title': title,
			'criteria': c
		}
		
		# add extra fields
		for fld in extra_fields:
			d[fld] = self.doc.get(fld)
		
		return d
	
	@property
	def eligibility_formatted(self):
		""" Puts the criteria in a human-readable format
		"""
		if self.doc is None:
			return "No eligibility data"
		
		# the main criteria
		elig = self.doc.get('eligibility')
		main = elig.get('criteria', {}).get('textblock')
		if len(self.criteria) > 0:
			inc = ['Inclusion Criteria:']
			exc = ['Exclusion Criteria:']
			for crit in self.criteria:
				if crit.get('is_inclusion', False):
					inc.append(crit.get('text'))
				else:
					exc.append(crit.get('text'))
			
			t_inc = "\n\t".join(inc)
			t_exc = "\n\t".join(exc)
			main = "%s\n\n%s" % (t_inc, t_exc)
		
		# additional bits
		return "Gender: %s\nAge: %s - %s\nHealthy: %s\n\n%s" % (
			elig.get('gender'),
			elig.get('minimum_age', 0),
			elig.get('maximum_age', 0),
			elig.get('healthy_volunteers'),
			main
		)
	
	
	def report_row(self):
		""" Generates an HTML row for the report_row document.
		"""
		if self.criteria is None or len(self.criteria) < 1:
			return ''
		
		# collect criteria
		rows = []
		snomed = SNOMEDLookup()
		rxnorm = RxNormLookup()
		umls = UMLSLookup()
		is_first = True
		for crit in self.criteria:
			css_class = '' if is_first else 'crit_first'
			in_ex = 'in' if crit.get('is_inclusion', False) else 'ex'
			
			# this criterion has been codified
			c_snomed_ct = crit.get('snomed', [])
			c_rx_ct = crit.get('rxnorm_ctakes', [])
			c_cui_mm = crit.get('cui_metamap', [])
			rspan = max(len(c_snomed_ct), len(c_rx_ct), len(c_cui_mm))
			if rspan > 0:
				
				c_html = """<td class="%s" rowspan="%d">%s</td>
				<td class="%s" rowspan="%d">%s</td>""" % (css_class, rspan, crit.get('text'), css_class, rspan, in_ex)
				
				# create cells
				for i in xrange(0, rspan):
					sno = c_snomed_ct[i] if len(c_snomed_ct) > i else ''
					rx = c_rx_ct[i] if len(c_rx_ct) > i else ''
					cui = c_cui_mm[i] if len(c_cui_mm) > i else ''
					
					if 0 == i:
						rows.append(c_html + """<td class="%s">%s</td>
						<td class="%s">%s</td>
						<td class="%s">%s</td>
						<td class="%s">%s</td>
						<td class="%s">%s</td>
						<td class="%s">%s</td>""" % (css_class, sno, css_class, snomed.lookup_code_meaning(sno), css_class, rx, css_class, rxnorm.lookup_code_meaning(rx, True), css_class, cui, css_class, umls.lookup_code_meaning(cui, True)))
					else:
						rows.append("""<td>%s</td>
						<td>%s</td>
						<td>%s</td>
						<td>%s</td>
						<td>%s</td>
						<td>%s</td>""" % (sno, snomed.lookup_code_meaning(sno), rx, rxnorm.lookup_code_meaning(rx, True), cui, umls.lookup_code_meaning(cui, True)))
			
			# no codes for this criterion
			else:
				rows.append("""<td class="%s">%s</td>
					<td class="%s">%s</td>
					<td class="%s"></td>
					<td class="%s"></td>
					<td class="%s"></td>
					<td class="%s"></td>
					<td class="%s"></td>
					<td class="%s"></td>
					<td class="%s"></td>""" % (css_class, crit.get('text'), css_class, in_ex, css_class, css_class, css_class, css_class, css_class, css_class, css_class))
			
			is_first = False
		
		if len(rows) < 1:
			return ''
		
		# compose HTML
		html = """<tr class="trial_first">
		<td rowspan="%d">
			<a href="http://clinicaltrials.gov/ct2/show/%s" target="_blank">%s</a>
		</td>
		<td rowspan="%d" onclick="toggle(this)">
			<div style="display:none;">%s</div>
		</td>
		%s</tr>""" % (len(rows), self.nct, self.nct, len(rows), self.eligibility_formatted, rows[0])
		
		rows.pop(0)
		for row in rows:
			html += "<tr>%s</tr>" % row
		
		return html
	
	
	# extract single criteria from plain text eligibility criteria
	def process_eligibility_from_text(self):
		""" Parses the textual inclusion/exclusion format into dictionaries
		stored in a "criteria" property.
		"""
		
		crit = []
		
		# split into inclusion and exclusion
		elig = self.doc.get('eligibility')
		if not elig:
			logging.info("No eligibility criteria for %s" % self.nct)
			return
		
		(inclusion, exclusion) = split_inclusion_exclusion(elig.get('criteria', {}).get('textblock'))
		
		# parsed by bulleted list, produce one criterion per item; we also could
		# concatenate them into one file each.
		for txt in inclusion:
			obj = {'id': uuid.uuid4(), 'is_inclusion': True, 'text': txt}
			crit.append(obj)
		
		for txt in exclusion:
			obj = {'id': uuid.uuid4(), 'is_inclusion': False, 'text': txt}
			crit.append(obj)
		
		self.criteria = crit
		self.store({'criteria': crit})
	
	
	def waiting_for_nlp(self, nlp_name):
		""" Returns True if any of our criteria needs to run through NLP.
		"""
		if 'ctakes' == nlp_name and self.waiting_for_ctakes_pmc:
			return True
		
		if len(self.criteria) > 0:
			for criterion in self.criteria:
				if nlp_name in criterion.get('waiting_for_nlp', []):
					return True
		
		return False
	
	
	# -------------------------------------------------------------------------- PubMed
	def run_pmc(self, run_dir):
		""" Finds, downloads, extracts and parses PMC-indexed publications for
		the trial. """
		self.find_pmc_packages()
		self.download_pmc_packages(run_dir)
		self.parse_pmc_packages(run_dir)
	
	
	def find_pmc_packages(self):
		""" Determine whether there was a PMC-indexed publication for the trial.
		"""
		if self.nct is None:
			logging.warning("Need an NCT before trying to find publications")
			return
		
		# find paper details
		self.papers = Paper.find_by_nct(self.nct)
		for paper in self.papers:
			paper.fetch_pmc_ids()
	
	
	def download_pmc_packages(self, run_dir):
		""" Downloads the PubMed Central packages for our papers. """
		
		if self.papers is not None:
			for paper in self.papers:
				paper.download_pmc_packages(run_dir)
	
	
	def parse_pmc_packages(self, run_dir):
		""" Looks for downloaded packages in the given run directory and
		extracts the paper text from the XML in the .nxml file.
		"""
		if self.papers is None:
			return
		
		if not os.path.exists(run_dir):
			raise Exception("The run directory %s doesn't exist" % run_dir)
		
		ct_in_dir = os.path.join(Study.ctakes.get('root', run_dir), 'ctakes_input')
		for paper in self.papers:
			paper.parse_pmc_packages(run_dir, ct_in_dir)
			
			# also dump CT criteria if the paper has methods
			if paper.has_methods:
				plaintextpath = os.path.join(ct_in_dir, "%s-%s-CT.txt" % (self.nct, paper.pmid))
				with codecs.open(plaintextpath, 'w', 'utf-8') as handle:
					handle.write(self.eligibility_formatted)
				
				self.waiting_for_ctakes_pmc = True
	
	
	# -------------------------------------------------------------------------- Codification
	def codify_eligibility(self):
		""" Retrieves the codes from the database or, if there are none, tries
		to parse NLP output or passes the text criteria to NLP.
		"""
		if self.criteria is not None:
			for criterion in self.criteria:
				self.criterion_codify(criterion)
	
	def criterion_codify(self, criterion):
		""" Three stages:
		      1. Reads the codes from SQLite, if they are there
		      2. Reads and stores the codes from the NLP output dir(s)
		      3. Writes the criteria to the NLP input directories and fills the
		         "waiting_for_nlp" list
		"""
		if self.nlp is None:
			return False
		
		for nlp in self.nlp:
			if not self.criterion_parse_nlp_output(criterion, nlp):
				self.criterion_write_nlp_input(criterion, nlp)
	
	def criterion_write_nlp_input(self, criterion, nlp):
		""" Writes the NLP engine input file and sets the waiting flag.
		It also sets the waiting flag if the file hasn't been written but there
		is yet no output. """
		waiting = False
		
		if nlp.write_input(criterion.get('text'), '%d.txt' % criterion.get('id')):
			waiting = True
		else:
			arr = criterion.get('cui_ctakes') if 'ctakes' == nlp.name else criterion.get('cui_metamap')
			if not arr or len(arr) < 1:
				waiting = True
		
		# waiting for NLP processing?
		if waiting:
			criterion.get('waiting_for_nlp', []).append(nlp.name)
	
	def criterion_parse_nlp_output(self, criterion, nlp, force=False):
		""" Parses the NLP output file (currently understands cTAKES and MetaMap
		output) and stores the codes in the database. """
		
		# skip parsing if we already did parse before
		if 'ctakes' == nlp.name:
			if criterion.get('snomed') is not None:
				return True
		elif 'metamap' == nlp.name:
			if criterion.get('cui_metamap') is not None:
				return True
		
		# parse our file; if it doesn't return a result we'll return False which
		# will cause us to write to the NLP engine's input
		filename = '%d.txt' % criterion.get('id')
		ret = nlp.parse_output(filename, filter_sources=True)
		if ret is None:
			return False
		
		# got cTAKES data
		if 'ctakes' == nlp.name:
			if 'snomed' in ret:
				criterion['snomed'] = ret.get('snomed', [])
			if 'cui' in ret:
				criterion['cui_ctakes'] = ret.get('cui', [])
			if 'rxnorm' in ret:
				criterion['rxnorm_ctakes'] = ret.get('rxnorm', [])
		
		# got MetaMap data
		elif 'metamap' == nlp.name:
			if 'cui' in ret:
				criterion['cui_metamap'] = ret.get('cui', [])
		
		# no longer waiting
		wait = criterion.get('waiting_for_nlp')
		if wait is not None and nlp.name in wait:
			wait.remove(nlp.name)
			criterion['waiting_for_nlp'] = wait
		
		return True
	
	
	# -------------------------------------------------------------------------- Class Methods
	collection_name = 'studies'
	
	@classmethod
	def setup_ctakes(cls, setting):
		cls.ctakes = setting
	
	@classmethod
	def setup_metamap(cls, setting):
		cls.metamap = setting
		
	
	# -------------------------------------------------------------------------- Utilities
	def __unicode__(self):
		return '<study.Study %s>' % (self.id)
	
	def __str__(self):
		return unicode(self).encode('utf-8')
	
	def __repr__(self):
		return str(self)
	

