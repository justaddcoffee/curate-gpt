from curate_gpt.agents.mapping_agent import MappingAgent
from curate_gpt.agents.concept_recognition_agent import ConceptRecognitionAgent


class ClinicalConceptMatcher:
    def __init__(self, mapping_agent: MappingAgent, concept_recognition_agent: ConceptRecognitionAgent):
        self.mapping_agent = mapping_agent
        self.concept_recognition_agent = concept_recognition_agent

    def find_best_matching_terms(self, clinical_variable: str) -> str:
        mappings = self.mapping_agent.match(clinical_variable, limit=10)
        best_match = None
        best_score = 0

        for mapping in mappings.mappings:
            match_text = f"{clinical_variable} // {mapping.object_id}"
            response = self.concept_recognition_agent.ground_concept(match_text)
            if response.score > best_score:
                best_match = mapping.object_id
                best_score = response.score

        return best_match
